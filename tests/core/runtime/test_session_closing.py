import asyncio

import pytest

from harnessed import Agent, Plugin
from harnessed.errors import SessionStateError
from harnessed.hooks import SessionClosing
from harnessed.kernel import PluginScope
from harnessed.models import ModelChunk
from harnessed.runtime.control import SessionStatus
from harnessed.testing import CallbackModel, ScriptedModel


@pytest.mark.parametrize("cancel_first", [False, True])
async def test_concurrent_close_waits_for_shared_resource_cleanup(cancel_first):
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    observed = []

    class Resources(Plugin):
        async def setup(self, scope: PluginScope) -> None:
            async def closing(event):
                observed.append(event.session.status)
                started.set()
                await release.wait()
                finished.set()

            scope.on(SessionClosing, closing)

    async with Agent(model=ScriptedModel([]), plugins=[Resources()]).session() as session:
        first = asyncio.create_task(session.aclose())
        await asyncio.wait_for(started.wait(), 2)
        if cancel_first:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(first, timeout=5)
        second = asyncio.create_task(session.aclose())
        try:
            await asyncio.sleep(0)
            assert not second.done()
        finally:
            release.set()
            await asyncio.gather(first, second, return_exceptions=True)
        assert finished.is_set()
        assert observed == [SessionStatus.CLOSED]


async def test_explicit_close_dispatches_resource_cleanup_once():
    observed = []

    class Resources(Plugin):
        async def setup(self, scope: PluginScope) -> None:
            scope.on(SessionClosing, lambda event: observed.append(event.session.status))

    async with Agent(model=ScriptedModel([]), plugins=[Resources()]).session() as session:
        await session.aclose()
        await session.aclose()
        assert observed == [SessionStatus.CLOSED]
    assert observed == [SessionStatus.CLOSED]


async def test_closing_hook_cannot_wait_for_its_own_cleanup():
    observed = []

    class Resources(Plugin):
        async def setup(self, scope: PluginScope) -> None:
            async def closing(event):
                with pytest.raises(SessionStateError):
                    await session.aclose()
                observed.append(event.session.status)

            scope.on(SessionClosing, closing)

    async with Agent(model=ScriptedModel([]), plugins=[Resources()]).session() as session:
        await asyncio.wait_for(session.aclose(), 2)
        assert observed == [SessionStatus.CLOSED]


async def test_rejected_driver_close_does_not_release_live_resources():
    observed = []

    class Resources(Plugin):
        async def setup(self, scope: PluginScope) -> None:
            scope.on(SessionClosing, lambda event: observed.append(event.session.id))

    async def respond(request):
        with pytest.raises(SessionStateError):
            await session.aclose()
        assert observed == []
        yield ModelChunk(text="still open")

    async with Agent(model=CallbackModel(respond), plugins=[Resources()]).session() as session:
        assert (await session.run("Try closing from the driver")).text == "still open"
        await session.aclose()
        assert observed == [session.id]
