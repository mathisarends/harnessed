import asyncio

import pytest

from harnessed import Agent, Compaction, Context, Plugin, tool
from harnessed.errors import SessionFormatError
from harnessed.events import CompactionAppliedEvent, ModelRequestedEvent, TurnEndedEvent
from harnessed.models import ImageReference, UserMessage
from harnessed.runtime.control import SessionStatus
from harnessed.sessions import SessionCheckpoint, SessionCommit, SessionExecution, SessionState
from harnessed.testing import ExtractiveSummarizer, ScriptedModel, call


class SplitStore:
    """Store the state only when it changed and the execution part at every commit."""

    def __init__(self) -> None:
        self.commits: list[SessionCommit] = []
        self.states: dict[str, dict] = {}
        self.executions: dict[str, dict] = {}

    async def __call__(self, commit: SessionCommit) -> None:
        self.commits.append(commit)
        if commit.state_changed:
            self.states[commit.session_id] = commit.checkpoint.state.to_dict()
        self.executions[commit.session_id] = commit.checkpoint.execution.to_dict()

    def latest(self, session_id: str) -> SessionCheckpoint:
        return SessionCheckpoint.from_parts(
            SessionState.from_dict(self.states[session_id]),
            SessionExecution.from_dict(self.executions[session_id]),
        )


class Look(Plugin):
    @tool(readonly=True)
    async def look(self) -> str:
        """Look around."""
        return "Seen."


def _ends_turn(commit: SessionCommit) -> bool:
    return any(isinstance(change.event, TurnEndedEvent) for change in commit.changes)


async def test_only_turn_ends_compaction_and_the_first_commit_change_the_state():
    compaction = Compaction(
        trigger=lambda _: False,
        keep_last_steps=1,
        summarizer=ExtractiveSummarizer(),
        max_summary_tokens=8,
    )
    store = SplitStore()
    model = ScriptedModel([call("look"), "first", call("look"), "second"])
    agent = Agent(model=model, plugins=[Look()], context=Context(compaction=compaction))
    async with agent.session(on_commit=store) as session:
        await session.run("old context " * 500)
        await session.run("two")
        assert await session.compact() is not None
    first, *rest = store.commits
    assert first.state_changed
    in_turn = [commit for commit in rest if commit.checkpoint.events]
    assert in_turn and not any(commit.state_changed for commit in in_turn)
    assert [commit.state_changed for commit in rest if _ends_turn(commit)] == [True, True]
    (compacted,) = [
        commit
        for commit in rest
        if any(isinstance(change.event, CompactionAppliedEvent) for change in commit.changes)
    ]
    assert compacted.state_changed
    idle = [commit for commit in rest if not commit.checkpoint.events and not _ends_turn(commit)]
    assert [commit.state_changed for commit in idle if commit is not compacted] == [False]


async def test_state_and_execution_stored_apart_reopen_a_paused_turn():
    entered, release = asyncio.Event(), asyncio.Event()

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            """Work."""
            entered.set()
            await release.wait()
            return "done"

    store = SplitStore()
    agent = Agent(model=ScriptedModel(["warm up", call("work"), "finished"]), plugins=[Work()])
    async with agent.session(id="split", on_commit=store) as session:
        await session.run("Warm up")
        handle = await session.submit("Work", max_steps=2)
        await asyncio.wait_for(entered.wait(), timeout=5)
        pausing = asyncio.create_task(session.pause())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(pausing, timeout=5)
        turn_id = session.log.open_turn.id
    del handle
    restored_checkpoint = store.latest("split")
    assert restored_checkpoint.events
    async with agent.session(state=restored_checkpoint) as restored:
        assert restored.status is SessionStatus.PAUSED
        assert restored.log.open_turn.id == turn_id
        await restored.resume()
        await restored.wait_until_idle()
        assert restored.messages[-1].text == "finished"


async def test_an_execution_from_another_state_is_rejected():
    store = SplitStore()
    async with Agent(model=ScriptedModel(["one", "two"])).session(on_commit=store) as session:
        await session.run("first")
        earlier = session.checkpoint().state
        await session.run("second")
        execution = session.checkpoint().execution
    with pytest.raises(SessionFormatError, match="different conversation state"):
        SessionCheckpoint.from_parts(earlier, execution)
    document = execution.to_dict()
    document["offset"] = 0
    with pytest.raises(SessionFormatError):
        SessionExecution.from_dict(document)


async def test_in_turn_execution_size_does_not_grow_with_retained_images():
    store = SplitStore()
    model = ScriptedModel(["seen"] * 6)
    async with Agent(model=model).session(on_commit=store) as session:
        sizes = []
        for index in range(6):
            data = bytes([index]) * 64_000
            ref = await session.assets.put(data, media_type="image/png")
            start = len(store.commits)
            await session.run(UserMessage((ImageReference(ref),)))
            first_request = next(
                commit
                for commit in store.commits[start:]
                if any(isinstance(change.event, ModelRequestedEvent) for change in commit.changes)
            )
            sizes.append(len(first_request.checkpoint.execution.to_json()))
        state_size = len(session.checkpoint().state.to_json())
    # Each turn adds its own image to the execution part; earlier ones stay in the state.
    # Only the recorded request's references to earlier images and messages grow.
    assert state_size > 6 * 64_000
    assert max(sizes) - min(sizes) < 64_000 // 10
