import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Literal

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

import harnessed.core.session.store as persistence_store
from harnessed import Agent, Plugin
from harnessed.core.prompt.context import DynamicContext
from harnessed.errors import SessionWriteConflict
from harnessed.events import (
    CompactionAppliedEvent,
    ContextMessageEvent,
    DurableEvent,
    ReasoningDeltaEvent,
    SessionEvent,
    TextDeltaEvent,
    ToolArgumentsDeltaEvent,
    UserMessageEvent,
)
from harnessed.hooks import SessionAppended
from harnessed.models import Message, ModelChunk, ModelRequest, ToolCallDelta, UserMessage
from harnessed.plugins import Compaction, every_turns
from harnessed.sessions import (
    CheckpointExtension,
    SessionChange,
    SessionCommit,
    StoredSession,
)
from harnessed.testing import CallbackModel, ScriptedModel
from tests._checkpoints import checkpoint


class RecordingStore:
    def __init__(self) -> None:
        self.commits: list[SessionCommit] = []
        self.rows: dict[str, StoredSession] = {}

    async def load_session(self, conversation_id: str) -> StoredSession | None:
        return self.rows.get(conversation_id)

    async def save_session(
        self, conversation_id: str, commit: SessionCommit, *, expected_revision: int | None
    ) -> int:
        stored = self.rows.get(conversation_id)
        revision = stored.revision if stored is not None else None
        if revision != expected_revision:
            raise SessionWriteConflict(conversation_id)
        next_revision = 1 if revision is None else revision + 1
        self.rows[conversation_id] = StoredSession(commit.checkpoint, next_revision)
        self.commits.append(commit)
        return next_revision


class Brief:
    async def summarize(self, messages: tuple[Message, ...], *, max_tokens: int) -> str:
        return "Earlier conversation."


@dataclass(frozen=True)
class IndexedEvent(DurableEvent):
    files: int
    type: Literal["project.indexed"] = field(default="project.indexed", kw_only=True)


async def test_token_deltas_are_live_without_scheduling_or_entering_commits(monkeypatch):
    monkeypatch.setattr(persistence_store, "_CHECKPOINT_INTERVAL", 0.01)
    store = RecordingStore()
    entered, start = asyncio.Event(), asyncio.Event()
    ready, release = asyncio.Event(), asyncio.Event()
    before_tokens: list[int] = []
    live: list[TextDeltaEvent | ReasoningDeltaEvent | ToolArgumentsDeltaEvent] = []

    async def tokens(_request: ModelRequest) -> AsyncIterator[ModelChunk]:
        entered.set()
        await asyncio.wait_for(start.wait(), timeout=5)
        before_tokens.append(len(store.commits))
        for _ in range(1000):
            yield ModelChunk(
                text="x", reasoning="r", tool_call_delta=ToolCallDelta(0, "{}", "preview")
            )
        ready.set()
        await asyncio.wait_for(release.wait(), timeout=5)

    def observe(event: TextDeltaEvent | ReasoningDeltaEvent | ToolArgumentsDeltaEvent) -> None:
        live.append(event)

    async with Agent(model=CallbackModel(tokens), store=store).session(id="stream") as session:
        session.on(TextDeltaEvent, observe)
        session.on(ReasoningDeltaEvent, observe)
        session.on(ToolArgumentsDeltaEvent, observe)
        handle = await session.submit("Stream")
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            # Settle request/observation metadata before measuring token-only traffic.
            await session.flush()
            start.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
            assert len(live) == 3000
            # Cross several batching intervals while only the live draft changes.
            await asyncio.sleep(0.05)
            assert len(store.commits) == before_tokens[0]
            assert not any(
                isinstance(
                    change.event, TextDeltaEvent | ReasoningDeltaEvent | ToolArgumentsDeltaEvent
                )
                for commit in store.commits
                for change in commit.changes
            )
            assert "x" * 1000 not in store.rows["stream"].checkpoint.to_json()
        finally:
            release.set()
        result = await asyncio.wait_for(handle.result(), timeout=5)
        assert result.text == "x" * 1000
        assert store.rows["stream"].checkpoint.state.history[-1].message.text == result.text
        assert len(store.commits) < 10


async def test_commits_cover_each_live_event_once_even_after_compaction_and_reopening():
    store = RecordingStore()
    model = ScriptedModel(["first reply", "second reply", "third reply"])
    agent = Agent(
        model=model,
        store=store,
        plugins=[Compaction(trigger=every_turns(1), keep_turns=0, summarizer=Brief())],
    )
    observed: list[tuple[int, SessionEvent]] = []

    def observe(event: SessionAppended) -> None:
        observed.append((event.seq, event.event))

    first_input = "first input " * 500
    async with agent.session(id="archive") as session:
        session.on(SessionAppended, observe)
        await session.run(first_input)
        await session.run("second input")
        assert any(isinstance(event, CompactionAppliedEvent) for _, event in observed)
        assert first_input not in [entry.message.text for entry in session.snapshot().history]
        changes = [change for commit in store.commits for change in commit.changes]
        assert [(change.sequence, change.event) for change in changes] == observed

    before = len(store.commits)
    boundary = store.commits[-1].next_sequence
    async with agent.session(id="archive") as restored:
        assert len(store.commits) == before
        await restored.run("third input")
        new = [change for commit in store.commits[before:] for change in commit.changes]
        assert new and new[0].sequence == boundary
        assert not any(
            isinstance(change.event, UserMessageEvent)
            and change.event.message.text in {first_input, "second input"}
            for change in new
        )


async def test_coalescing_retains_ordered_changes_and_empty_flush_does_not_write(monkeypatch):
    monkeypatch.setattr(persistence_store, "_CHECKPOINT_INTERVAL", 60.0)
    store = RecordingStore()
    async with Agent(model=ScriptedModel([]), store=store).session(id="batch") as session:
        await session.ctx[DynamicContext].set("seed", "ready")
        before = len(store.commits)
        await session.emit(ContextMessageEvent(UserMessage("first")))
        await session.emit(ContextMessageEvent(UserMessage("second")))
        assert len(store.commits) == before
        await session.flush()
        commit = store.commits[-1]
        assert len(store.commits) == before + 1
        assert [change.sequence for change in commit.changes] == [1, 2]
        assert all(isinstance(change.event, ContextMessageEvent) for change in commit.changes)
        assert [entry.message.text for entry in commit.checkpoint.state.history][-2:] == [
            "first",
            "second",
        ]
        await session.flush()
        assert len(store.commits) == before + 1


async def test_idle_custom_events_are_available_as_changes_without_a_store_catalog():
    class Indexer(Plugin):
        events = (IndexedEvent,)

    store = RecordingStore()
    async with Agent(
        model=ScriptedModel([]), store=store, plugins=[Indexer()]
    ).session() as session:
        await session.emit(IndexedEvent(42))
        commit = store.commits[-1]
        assert not commit.checkpoint.events
        assert len(commit.changes) == 1
        change = commit.changes[0]
        assert isinstance(change.event, CheckpointExtension)
        assert change.event.tag == "project.indexed" and change.event.data == {"files": 42}
        adapter = TypeAdapter(SessionCommit)
        assert adapter.validate_json(adapter.dump_json(commit)) == commit


@pytest.mark.parametrize("sequences", [(0, 0), (1, 0), (0, 2)])
def test_commit_rejects_duplicate_reversed_or_gapped_changes(sequences):
    event = ContextMessageEvent(UserMessage("fact"))
    with pytest.raises(ValidationError, match="contiguous"):
        SessionCommit(
            replace(checkpoint(), offset=3),
            tuple(SessionChange(sequence, event) for sequence in sequences),
        )


def test_commit_validates_its_checkpoint_boundary_and_nested_backend_schema():
    class BackendEnvelope(BaseModel):
        commit: SessionCommit

    event = ContextMessageEvent(UserMessage("fact"))
    with pytest.raises(ValidationError, match="next sequence"):
        SessionCommit(checkpoint(), (SessionChange(0, event),))
    value = SessionCommit(replace(checkpoint(), offset=1), (SessionChange(0, event),))
    envelope = BackendEnvelope(commit=value)
    assert BackendEnvelope.model_validate_json(envelope.model_dump_json()).commit == value
    assert "SessionChange" in BackendEnvelope.model_json_schema()["$defs"]
