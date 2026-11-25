import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from harnessed import Agent, Compaction, Context, Plugin
from harnessed.context import every_turns
from harnessed.context.compaction import CompactionRequest, Summary
from harnessed.events import (
    CompactionAppliedEvent,
    DurableEvent,
    MessageAppendedEvent,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolArgumentsDeltaEvent,
    UserMessageEvent,
)
from harnessed.models import ModelChunk, ModelRequest, ToolCallDelta, UserMessage
from harnessed.runtime.hooks import SessionAppended
from harnessed.sessions import CheckpointExtension, JournalEntry, SessionCommit
from harnessed.sessions.values import PluginValues
from harnessed.testing import CallbackModel, ScriptedModel
from tests._checkpoints import boundary
from tests._commits import CommitRecorder


class Brief:
    async def summarize(self, request: CompactionRequest) -> Summary:
        return Summary("Earlier conversation.")


@dataclass(frozen=True)
class IndexedEvent(DurableEvent):
    files: int
    type: Literal["project.indexed"] = field(default="project.indexed", kw_only=True)


async def test_token_deltas_are_live_without_entering_commits():
    store = CommitRecorder()
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

    async with Agent(model=CallbackModel(tokens)).session(id="stream", store=store) as session:
        session.scope.on(TextDeltaEvent, observe)
        session.scope.on(ReasoningDeltaEvent, observe)
        session.scope.on(ToolArgumentsDeltaEvent, observe)
        handle = await session.submit("Stream")
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            # Settle request/observation metadata before measuring token-only traffic.
            await session.flush()
            start.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
            assert len(live) == 3000
            # Give a stray delivery time to happen while only the live draft changes.
            await asyncio.sleep(0.05)
            assert len(store.commits) == before_tokens[0]
            assert not any(
                isinstance(
                    entry.record, TextDeltaEvent | ReasoningDeltaEvent | ToolArgumentsDeltaEvent
                )
                for commit in store.commits
                for entry in commit.journal
            )
            assert "x" * 1000 not in store.latest("stream").to_json()
        finally:
            release.set()
        result = await asyncio.wait_for(handle.result(), timeout=5)
        assert result.text == "x" * 1000
        assert store.latest("stream").state.history[-1].message.text == result.text
        assert len(store.commits) < 10


async def test_commits_cover_each_live_event_once_even_after_compaction_and_reopening():
    store = CommitRecorder()
    model = ScriptedModel(["first reply", "kept", "second reply", "third reply"])
    agent = Agent(
        model=model,
        context=Context(
            compaction=Compaction(trigger=every_turns(1), keep_last_steps=1, summarizer=Brief())
        ),
    )
    observed: list[tuple[int, DurableEvent]] = []

    def observe(event: SessionAppended) -> None:
        observed.append((event.seq, event.event))

    first_input = "first input " * 500
    async with agent.session(id="archive", store=store) as session:
        session.scope.on(SessionAppended, observe)
        await session.run(first_input)
        await session.run("kept")
        await session.run("second input")
        assert any(isinstance(event, CompactionAppliedEvent) for _, event in observed)
        assert first_input not in [entry.message.text for entry in session.snapshot().history]
        entries = [entry for commit in store.commits for entry in commit.journal]
        assert [entry.sequence for entry in entries] == [seq for seq, _ in observed]
        assert [entry.record.type for entry in entries] == [
            "checkpoint.request" if event.type == "model.requested" else event.type
            for _, event in observed
        ]

    before = len(store.commits)
    next_sequence = store.commits[-1].next_sequence
    async with agent.session(id="archive", store=store) as restored:
        assert len(store.commits) == before
        await restored.run("third input")
        new = [entry for commit in store.commits[before:] for entry in commit.journal]
        assert new and new[0].sequence == next_sequence
        assert not any(
            isinstance(entry.record, UserMessageEvent)
            and entry.record.message.text in {first_input, "second input"}
            for entry in new
        )


async def test_pending_changes_keep_their_order_and_empty_flush_does_not_deliver():
    store = CommitRecorder()
    async with Agent(model=ScriptedModel([])).session(store=store) as session:
        await session.scope[PluginValues].commit("seed", "ready")
        before = len(store.commits)
        await session.emit(MessageAppendedEvent(UserMessage("first")))
        await session.emit(MessageAppendedEvent(UserMessage("second")))
        assert len(store.commits) == before
        await session.flush()
        commit = store.commits[-1]
        assert len(store.commits) == before + 1
        assert [entry.sequence for entry in commit.journal] == [1, 2]
        assert all(isinstance(entry.record, MessageAppendedEvent) for entry in commit.journal)
        assert commit.boundary is not None
        assert [entry.message.text for entry in commit.boundary.state.history][-2:] == [
            "first",
            "second",
        ]
        await session.flush()
        assert len(store.commits) == before + 1


async def test_idle_custom_events_are_available_as_changes_without_a_store_catalog():
    class Indexer(Plugin):
        events = (IndexedEvent,)

    store = CommitRecorder()
    async with Agent(model=ScriptedModel([]), plugins=[Indexer()]).session(store=store) as session:
        await session.emit(IndexedEvent(42))
        commit = store.commits[-1]
        assert not store.latest(session.id).events
        (entry,) = commit.journal
        assert isinstance(entry.record, CheckpointExtension)
        assert entry.record.tag == "project.indexed" and entry.record.data == {"files": 42}
        adapter = TypeAdapter(SessionCommit)
        assert adapter.validate_json(adapter.dump_json(commit)) == commit


@pytest.mark.parametrize("sequences", [(0, 0), (1, 0), (0, 2)])
def test_commit_rejects_duplicate_reversed_or_gapped_entries(sequences):
    event = MessageAppendedEvent(UserMessage("fact"))
    with pytest.raises(ValidationError, match="contiguous"):
        SessionCommit("session", tuple(JournalEntry(sequence, event) for sequence in sequences))


def test_commit_validates_its_boundary_and_nested_backend_schema():
    class BackendEnvelope(BaseModel):
        commit: SessionCommit

    entry = JournalEntry(0, MessageAppendedEvent(UserMessage("fact")))
    with pytest.raises(ValidationError, match="at least one"):
        SessionCommit("session", ())
    with pytest.raises(ValidationError, match="beyond its journal"):
        SessionCommit("session", (entry,), boundary=boundary(sequence=2))
    with pytest.raises(ValidationError, match="its session"):
        SessionCommit("other", (entry,), boundary=boundary(sequence=1))
    value = SessionCommit("session", (entry,), boundary=boundary(sequence=1))
    envelope = BackendEnvelope(commit=value)
    assert BackendEnvelope.model_validate_json(envelope.model_dump_json()).commit == value
    assert "JournalEntry" in BackendEnvelope.model_json_schema()["$defs"]
