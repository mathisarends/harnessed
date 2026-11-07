import asyncio

import pytest
from pydantic import ValidationError

from harnessed import Plugin, ToolCall, ToolStatus, tool
from harnessed.core.tools.registry import ToolRegistry
from harnessed.kernel import Scope, needs


async def test_timeout_resolves_after_setup_once_per_activation() -> None:
    resolved = []

    class Work(Plugin, provides=True):
        configured_seconds = needs(float)

        def __init__(self) -> None:
            super().__init__()
            self.seconds = 60.0

        async def setup(self, scope: Scope) -> None:
            self.seconds = self.configured_seconds

        def _timeout(self) -> float:
            resolved.append(self.seconds)
            return self.seconds

        @tool(timeout_seconds=_timeout)
        async def wait(self) -> str:
            await asyncio.Event().wait()
            return "unreachable"

    async with Scope() as scope:
        registry = ToolRegistry(scope)
        scope.provide(ToolRegistry, registry)
        scope.provide(float, 0.01)
        await scope.plugin(Work())
        assert resolved == [0.01]
        scope[Work].seconds = 60.0
        first = await asyncio.wait_for(registry.execute(ToolCall("wait")), timeout=3)
        second = await asyncio.wait_for(registry.execute(ToolCall("wait")), timeout=3)
        assert first.status is ToolStatus.TIMEOUT and second.status is ToolStatus.TIMEOUT
        assert resolved == [0.01]
        scope.provide(float, 0.02)
        await scope.settle()
        assert resolved == [0.01, 0.02]
        restarted = await asyncio.wait_for(registry.execute(ToolCall("wait")), timeout=3)
        assert restarted.status is ToolStatus.TIMEOUT


async def test_shared_recipe_keeps_each_mounts_resolved_timeout() -> None:
    class Work(Plugin, provides=True):
        seconds = needs(float)

        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        @tool(timeout_seconds=lambda self: self.seconds)
        async def wait(self) -> str:
            self.started.set()
            await self.release.wait()
            return "released"

    recipe = Work()
    async with Scope() as fast, Scope() as slow:
        fast_registry = ToolRegistry(fast)
        slow_registry = ToolRegistry(slow)
        fast.provide(ToolRegistry, fast_registry)
        slow.provide(ToolRegistry, slow_registry)
        fast.provide(float, 0.01)
        slow.provide(float, 60.0)
        await fast.plugin(recipe)
        await slow.plugin(recipe)
        async with asyncio.TaskGroup() as tasks:
            running = tasks.create_task(slow_registry.execute(ToolCall("wait")))
            try:
                await asyncio.wait_for(slow[Work].started.wait(), timeout=3)
                timed_out = await asyncio.wait_for(
                    fast_registry.execute(ToolCall("wait")), timeout=3
                )
                assert timed_out.status is ToolStatus.TIMEOUT and not running.done()
            finally:
                slow[Work].release.set()
            result = await asyncio.wait_for(running, timeout=3)
        assert result.status is ToolStatus.OK and result.text == "released"


async def test_resolver_none_inherits_the_execution_default() -> None:
    class Work(Plugin):
        def _timeout(self) -> None:
            return None

        @tool(timeout_seconds=_timeout)
        async def wait(self) -> str:
            await asyncio.Event().wait()
            return "unreachable"

    async with Scope() as scope:
        registry = ToolRegistry(scope)
        scope.provide(ToolRegistry, registry)
        await scope.plugin(Work())
        result = await asyncio.wait_for(
            registry.execute(ToolCall("wait"), default_timeout=0.01), timeout=3
        )
        assert result.status is ToolStatus.TIMEOUT


@pytest.mark.parametrize("seconds", [0, -1, True, "1", float("nan"), float("inf")])
async def test_invalid_resolved_timeouts_fail_before_registration(seconds) -> None:
    class Work(Plugin):
        @tool(timeout_seconds=lambda self: seconds)
        async def work(self) -> str:
            pytest.fail("Invalid timeout reached the tool")

    async with Scope() as scope:
        registry = ToolRegistry(scope)
        scope.provide(ToolRegistry, registry)
        with pytest.raises(ValidationError):
            await scope.plugin(Work())
        assert registry.definitions == ()


@pytest.mark.parametrize("wrapped", [False, True])
async def test_async_timeout_resolvers_are_rejected(wrapped: bool) -> None:
    async def asynchronous(owner: object) -> float:
        return 1.0

    def resolve(owner: object):
        return asynchronous(owner)

    class Work(Plugin):
        @tool(timeout_seconds=resolve if wrapped else asynchronous)
        async def work(self) -> str:
            pytest.fail("Async timeout reached the tool")

    async with Scope() as scope:
        registry = ToolRegistry(scope)
        scope.provide(ToolRegistry, registry)
        with pytest.raises(TypeError, match="Tool timeout_seconds resolvers must"):
            await scope.plugin(Work())
        assert registry.definitions == ()


def test_timeout_resolver_requires_a_mounted_plugin_method() -> None:
    def work() -> str:
        return "unused"

    registry = ToolRegistry(Scope())
    with pytest.raises(TypeError, match="require a mounted plugin method"):
        registry.register(work, timeout_seconds=lambda owner: 1.0)
    assert registry.definitions == ()
