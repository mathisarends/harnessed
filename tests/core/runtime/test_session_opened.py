import asyncio
from time import monotonic

import pytest

from harnessed import Agent, Plugin
from harnessed.events import TurnEndedEvent, TurnOutcome
from harnessed.hooks import SessionOpened
from harnessed.kernel import PluginScope, hook, needs
from harnessed.models import ModelChunk, UserMessage
from harnessed.sessions import SessionRecovery
from harnessed.sessions.log import SessionLog
from harnessed.testing import CallbackModel, ScriptedModel
from tests._commits import CommitRecorder


async def test_session_opened_observes_the_restored_context_before_entry():
    openings: list[SessionOpened] = []

    class Clock(Plugin, provides=True):
        started: float

        @hook(SessionOpened)
        def opened(self, event: SessionOpened) -> None:
            self.started = event.at
            openings.append(event)

    store = CommitRecorder(documents=True)
    agent = Agent(model=ScriptedModel([]), plugins=[Clock()])
    before = monotonic()
    async with agent.session(id="case", store=store) as session:
        first = session.scope[Clock]
        (opened,) = openings
        assert before <= first.started == opened.at <= monotonic()
        assert opened.session is session.plugin_context
        await session.append_message(UserMessage("existing history"))
    async with agent.session(state=store.latest("case")) as restored:
        _, reopened = openings
        assert reopened.session is restored.plugin_context
        assert reopened.session.messages[-1].text == "existing history"
        assert first is not restored.scope[Clock]
        assert first.started <= restored.scope[Clock].started == reopened.at


async def test_open_hook_failure_releases_resources_before_propagating():
    released = []

    class Broken(Plugin):
        async def setup(self, scope: PluginScope) -> None:
            scope.effect(lambda: released.append(True))

        @hook(SessionOpened)
        def fail_open(self, event: SessionOpened) -> None:
            raise ValueError("open failed")

    agent = Agent(model=ScriptedModel([]), plugins=[Broken()])
    with pytest.raises(ValueError, match="open failed"):
        async with agent.session():
            pytest.fail("Session body ran after an opening failure")
    assert released == [True]


async def _interrupted_checkpoint() -> SessionRecovery:
    started, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        started.set()
        await release.wait()
        yield ModelChunk(text="late")

    async with Agent(model=CallbackModel(respond)).session() as session:
        await session.submit("go")
        await asyncio.wait_for(started.wait(), timeout=5)
        checkpoint = session.recovery()
        release.set()
    assert checkpoint.state is not None
    return checkpoint


async def test_reopening_repairs_the_interrupted_turn_after_every_plugin_is_active():
    observed: list[str] = []

    class Watch(Plugin, needs=(SessionLog,)):
        log = needs(SessionLog)

        @hook(TurnEndedEvent)
        def ended(self, event: TurnEndedEvent) -> None:
            observed.append(f"turn.ended({event.reason})")

        @hook(SessionOpened)
        def opened(self, event: SessionOpened) -> None:
            observed.append(f"session.opened(open_turn={self.log.open_turn})")

    checkpoint = await _interrupted_checkpoint()
    store = CommitRecorder()
    store.put(checkpoint)
    agent = Agent(model=ScriptedModel([]), plugins=[Watch()])
    async with agent.session(id=checkpoint.session_id, store=store):
        assert observed == [
            f"turn.ended({TurnOutcome.ABORTED})",
            "session.opened(open_turn=None)",
        ]
        assert any(
            isinstance(entry.record, TurnEndedEvent) and entry.record.reason is TurnOutcome.ABORTED
            for entry in store.entries(checkpoint.session_id)
        )
