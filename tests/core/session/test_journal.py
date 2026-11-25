import asyncio
from dataclasses import replace

import pytest

from harnessed import Agent, Compaction, Context, Plugin, tool
from harnessed.errors import SessionFormatError
from harnessed.events import (
    CompactionAppliedEvent,
    InboxAddedEvent,
    MessageAppendedEvent,
    ModelRequestedEvent,
)
from harnessed.hooks import PrepareRequest
from harnessed.kernel import hook
from harnessed.models import (
    AssistantMessage,
    ImageReference,
    ModelResponse,
    ProviderState,
    UserMessage,
)
from harnessed.runtime.control import SessionStatus
from harnessed.sessions import (
    CheckpointRequest,
    JournalEntry,
    SessionBoundary,
    SessionCheckpoint,
    SessionCommit,
    SessionValue,
)
from harnessed.testing import ExtractiveSummarizer, ScriptedModel, call
from tests._commits import CommitRecorder


class Look(Plugin):
    @tool(readonly=True)
    async def look(self) -> str:
        """Look around."""
        return "Seen."


def _requests(checkpoint: SessionCheckpoint) -> list[CheckpointRequest]:
    return [
        entry.record for entry in checkpoint.journal if isinstance(entry.record, CheckpointRequest)
    ]


def _last_in_turn(store: CommitRecorder, session_id: str) -> SessionCheckpoint:
    """The stored checkpoint as it was at the last commit inside a turn."""
    checkpoint: SessionCheckpoint | None = None
    latest_in_turn: SessionCheckpoint | None = None
    for commit in store.commits:
        if commit.session_id == session_id:
            checkpoint = SessionCheckpoint.from_commit(commit, checkpoint)
            if checkpoint.journal:
                latest_in_turn = checkpoint
    assert latest_in_turn is not None
    return latest_in_turn


async def test_only_the_first_commit_turn_ends_and_idle_changes_carry_a_boundary():
    compaction = Compaction(
        trigger=lambda _: False,
        keep_last_steps=1,
        summarizer=ExtractiveSummarizer(),
        max_summary_tokens=8,
    )
    store = CommitRecorder()
    model = ScriptedModel([call("look"), "first", call("look"), "second"])
    agent = Agent(model=model, plugins=[Look()], context=Context(compaction=compaction))
    async with agent.session(on_commit=store) as session:
        await session.run("old context " * 500)
        await session.run("two")
        assert await session.compact() is not None
    first, *rest = store.commits
    assert first.boundary is not None

    def ends_turn(commit: SessionCommit) -> bool:
        return any(entry.record.type == "turn.ended" for entry in commit.journal)

    def compacts(commit: SessionCommit) -> bool:
        return any(isinstance(entry.record, CompactionAppliedEvent) for entry in commit.journal)

    assert [commit.boundary is not None for commit in rest if ends_turn(commit)] == [True, True]
    assert all(commit.boundary is not None for commit in rest if compacts(commit))
    in_turn = [commit for commit in rest if not ends_turn(commit) and not compacts(commit)]
    # Input admission and every barrier inside a turn only append entries.
    assert in_turn and all(commit.boundary is None for commit in in_turn)


async def test_entries_are_appended_once_and_continue_without_gaps():
    store = CommitRecorder()
    model = ScriptedModel([call("look"), "first", "second"])
    async with Agent(model=model, plugins=[Look()]).session(on_commit=store) as session:
        await session.run("one")
        await session.run("two")
    sequences = [entry.sequence for commit in store.commits for entry in commit.journal]
    assert sequences == list(range(len(sequences)))
    for commit in store.commits:
        if commit.boundary is not None:
            assert commit.boundary.sequence <= commit.next_sequence


async def test_idle_entries_after_the_boundary_are_replayed_when_reopening():
    store = CommitRecorder(documents=True)
    agent = Agent(model=ScriptedModel(["warm", "queued"]))
    async with agent.session(id="idle", on_commit=store) as session:
        await session.run("Warm up")
        await session.pause()
        handle = await session.submit("Queued while paused")
        latest = store.commits[-1]
        # Pausing and admitting input change no conversation state: entries only.
        assert latest.boundary is None
        assert isinstance(latest.journal[-1].record, InboxAddedEvent)
        del handle
    stored = store.latest("idle")
    assert [event.type for event in stored.events][:2] == ["session.control.changed", "inbox.added"]
    async with agent.session(state=stored) as restored:
        assert [item.content.text for item in restored.inbox.pending] == ["Queued while paused"]
        await restored.resume()
        await restored.wait_until_idle()
        assert restored.messages[-1].text == "queued"


async def test_a_paused_turn_reopens_from_its_boundary_and_journal():
    entered, release = asyncio.Event(), asyncio.Event()

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            """Work."""
            entered.set()
            await release.wait()
            return "done"

    store = CommitRecorder(documents=True)
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
    stored = store.latest("split")
    assert any(event.type == "turn.started" for event in stored.events)
    async with agent.session(state=stored) as restored:
        assert restored.status is SessionStatus.PAUSED
        assert restored.log.open_turn.id == turn_id
        await restored.resume()
        await restored.wait_until_idle()
        assert restored.messages[-1].text == "finished"


async def test_a_document_store_folds_commits_into_the_same_checkpoint():
    store = CommitRecorder()
    documents: dict[str, SessionCheckpoint] = {}

    async def fold(commit: SessionCommit) -> None:
        await store(commit)
        previous = documents.get(commit.session_id)
        documents[commit.session_id] = SessionCheckpoint.from_commit(commit, previous)

    model = ScriptedModel([call("look"), "first", "second"])
    async with Agent(model=model, plugins=[Look()]).session(id="doc", on_commit=fold) as session:
        await session.run("one")
        await session.run("two")
    assert documents["doc"] == store.latest("doc")
    assert documents["doc"].journal == ()


def test_a_journal_with_a_gap_or_from_another_session_is_rejected():
    boundary = SessionCheckpoint.from_json(
        '{"boundary": {"session_id": "s", "sequence": 2, "state": {},'
        ' "control": {"type": "session.control.changed", "state": "active"}}}'
    ).boundary
    gap = JournalEntry(3, MessageAppendedEvent(UserMessage("late")))
    with pytest.raises(SessionFormatError, match="does not continue"):
        SessionCheckpoint.from_journal(boundary, [gap])
    earlier = JournalEntry(1, MessageAppendedEvent(UserMessage("before")))
    assert SessionCheckpoint.from_journal(boundary, [earlier]).journal == ()
    other = replace(boundary, session_id="other")
    commit = SessionCommit("other", (JournalEntry(2, MessageAppendedEvent(UserMessage("x"))),))
    with pytest.raises(SessionFormatError, match="own session"):
        SessionCheckpoint.from_commit(commit, SessionCheckpoint(boundary))
    with pytest.raises(SessionFormatError, match="carries its boundary"):
        SessionCheckpoint.from_commit(commit)
    assert SessionCheckpoint.from_commit(commit, SessionCheckpoint(other)).journal == commit.journal


async def test_nested_plugin_value_type_changes_deliver_a_new_boundary():
    class Flags(Plugin, provides=True):
        value = SessionValue(dict[str, list[bool | int | float]], key="flag")

    store = CommitRecorder(documents=True)
    agent = Agent(model=ScriptedModel([]), plugins=[Flags()])
    async with agent.session(id="types", on_commit=store) as session:
        value = session.scope[Flags].value
        for scalar in (True, 1, 1.0):
            await value.set({"items": [scalar]})
            actual = store.latest("types").state.plugin_values["flag"]["items"][0]
            assert type(actual) is type(scalar)
        await value.set({"items": [1.0]})
    assert [commit.boundary is not None for commit in store.commits] == [True, True, True]


async def test_commit_size_inside_a_turn_does_not_grow_with_retained_images():
    store = CommitRecorder()
    model = ScriptedModel(["seen"] * 6)
    async with Agent(model=model).session(on_commit=store) as session:
        sizes = []
        for index in range(6):
            data = bytes([index]) * 64_000
            ref = await session.assets.put(data, media_type="image/png")
            start = len(store.commits)
            await session.run(UserMessage((ImageReference(ref),)))
            request = next(
                commit
                for commit in store.commits[start:]
                if any(isinstance(entry.record, CheckpointRequest) for entry in commit.journal)
            )
            sizes.append(sum(len(entry.to_json()) for entry in request.journal))
        state_size = len(session.checkpoint().state.to_json())
    # Each image travels once, with the commit after it was stored; earlier ones
    # stay in the boundary. Only the request's references to them grow.
    assert state_size > 6 * 64_000
    assert max(sizes) < 64_000 // 10


async def test_commit_size_inside_a_turn_does_not_grow_with_history_text():
    store = CommitRecorder()
    model = ScriptedModel(
        [
            *(f"answer {index} " + "y" * 4_000 for index in range(8)),
            call("look"),
            call("look"),
            "done",
        ]
    )
    sizes = []
    async with Agent(model=model, plugins=[Look()]).session(id="text", on_commit=store) as session:
        for index in range(8):
            start = len(store.commits)
            await session.run(f"question {index} " + "x" * 4_000)
            in_turn = [commit for commit in store.commits[start:] if commit.boundary is None]
            sizes.append(max(sum(len(e.to_json()) for e in c.journal) for c in in_turn))
        await session.run("look twice")
    # Eight turns add 64 KB of history; a commit carries only its own events and the
    # IDs of earlier messages, so the largest commit of each turn stays the same size.
    assert max(sizes) - min(sizes) < 1_000
    last = _last_in_turn(store, "text")
    requests = _requests(last)
    assert len(requests) == 3
    assert all(isinstance(slot, str) for slot in requests[-1].messages[:16])
    restored = SessionCheckpoint.from_json(last.to_json())
    assert restored == last and restored.events == last.events


async def test_requests_changed_by_a_hook_keep_the_messages_the_model_received():
    class Redact(Plugin):
        @hook(PrepareRequest)
        async def redact(self, event, next_):
            request = await next_()
            first, *rest = request.messages
            return replace(request, messages=(replace(first, content="[redacted]"), *rest))

    store = CommitRecorder()
    model = ScriptedModel(["one", "two"])
    async with Agent(model=model, plugins=[Redact()]).session(
        id="hook", on_commit=store
    ) as session:
        await session.run("secret")
        await session.run("again")
    last = _last_in_turn(store, "hook")
    (request,) = _requests(last)
    redacted, answer, question = request.messages
    # The changed history message is stored in full, the unchanged one by ID. The
    # open turn's input is not in the boundary yet, so its first request carries it.
    assert not isinstance(redacted, str) and redacted.text == "[redacted]"
    assert isinstance(answer, str)
    assert not isinstance(question, str) and question.text == "again"
    (recorded,) = [event for event in last.events if isinstance(event, ModelRequestedEvent)]
    assert recorded.request.messages == model.requests[1].messages


async def test_rebuilt_request_preserves_provider_state_changed_by_a_hook():
    class UpdateReplay(Plugin):
        @hook(PrepareRequest)
        async def update_replay(self, event, next_):
            request = await next_()
            messages = []
            for message in request.messages:
                if isinstance(message, AssistantMessage) and message.provider_state is not None:
                    changed = replace(message.provider_state, data={"flag": 1})
                    message = replace(message, provider_state=changed)
                messages.append(message)
            return replace(request, messages=tuple(messages))

    original = ProviderState(
        format="test", version=1, provider="test", model="test", data={"flag": True}
    )
    response = ModelResponse(AssistantMessage("first", provider_state=original))
    model = ScriptedModel([response, "second"])
    store = CommitRecorder()
    agent = Agent(model=model, plugins=[UpdateReplay()])
    async with agent.session(id="replay", on_commit=store) as session:
        await session.run("one")
        await session.run("two")
    restored = SessionCheckpoint.from_json(_last_in_turn(store, "replay").to_json())
    (event,) = [event for event in restored.events if isinstance(event, ModelRequestedEvent)]
    (reply,) = [
        message for message in event.request.messages if isinstance(message, AssistantMessage)
    ]
    flag = reply.provider_state.data["flag"]
    assert type(flag) is int and flag == 1


async def test_a_journal_referring_to_an_unknown_message_is_rejected():
    store = CommitRecorder()
    async with Agent(model=ScriptedModel(["one", "two"])).session(
        id="unknown", on_commit=store
    ) as session:
        await session.run("first")
        await session.run("second")
    last = _last_in_turn(store, "unknown")
    document = last.to_dict()
    (request,) = [
        entry["record"]
        for entry in document["journal"]
        if entry["record"]["type"] == "checkpoint.request"
    ]
    request["messages"][0] = "missing"
    with pytest.raises(SessionFormatError):
        SessionCheckpoint.from_dict(document)
    boundary = SessionBoundary.from_dict(document["boundary"])
    entries = [JournalEntry.from_dict(entry) for entry in document["journal"]]
    with pytest.raises(SessionFormatError, match="does not continue"):
        SessionCheckpoint.from_journal(boundary, entries)
