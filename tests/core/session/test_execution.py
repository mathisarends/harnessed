import asyncio
from dataclasses import replace

import pytest

from harnessed import Agent, Compaction, Context, Plugin, tool
from harnessed.errors import SessionFormatError
from harnessed.events import (
    CompactionAppliedEvent,
    ModelRequestedEvent,
    PluginValueChangedEvent,
    TurnEndedEvent,
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
    SessionCheckpoint,
    SessionCommit,
    SessionExecution,
    SessionState,
    SessionValue,
)
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


def _last_in_turn(store: SplitStore) -> SessionCommit:
    return next(commit for commit in reversed(store.commits) if commit.checkpoint.events)


def _requests(execution: SessionExecution) -> list[CheckpointRequest]:
    return [entry for entry in execution.events if isinstance(entry, CheckpointRequest)]


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


async def test_idle_value_changes_bind_execution_to_state_at_the_same_position():
    async with Agent(model=ScriptedModel([])).session() as session:
        await session.log.append(PluginValueChangedEvent("flag", "first"))
        stale = session.checkpoint().state
        await session.log.append(PluginValueChangedEvent("flag", {"a": 1, "b": 2}))
        current = session.checkpoint()
    execution = SessionExecution.from_json(current.execution.to_json())
    assert stale.next_sequence == current.state.next_sequence
    with pytest.raises(SessionFormatError, match="different conversation state"):
        SessionCheckpoint.from_parts(stale, execution)
    document = current.state.to_dict()
    document["plugin_values"] = {"flag": {"b": 2, "a": 1}}
    assert SessionCheckpoint.from_parts(SessionState.from_dict(document), execution) == current


async def test_split_store_persists_type_changes_in_nested_plugin_values():
    class Flags(Plugin, provides=True):
        value = SessionValue(dict[str, list[bool | int | float]], key="flag")

    store = SplitStore()
    agent = Agent(model=ScriptedModel([]), plugins=[Flags()])
    async with agent.session(id="types", on_commit=store) as session:
        value = session.scope[Flags].value
        for scalar in (True, 1, 1.0):
            await value.set({"items": [scalar]})
            actual = store.latest("types").state.plugin_values["flag"]["items"][0]
            assert type(actual) is type(scalar)
        await value.set({"items": [1.0]})
    assert [commit.state_changed for commit in store.commits] == [True, True, True]


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


async def test_in_turn_execution_size_does_not_grow_with_history_text():
    store = SplitStore()
    model = ScriptedModel(
        [
            *(f"answer {index} " + "y" * 4_000 for index in range(8)),
            call("look"),
            call("look"),
            "done",
        ]
    )
    sizes = []
    async with Agent(model=model, plugins=[Look()]).session(on_commit=store) as session:
        for index in range(8):
            await session.run(f"question {index} " + "x" * 4_000)
            sizes.append(len(_last_in_turn(store).checkpoint.execution.to_json()))
        await session.run("look twice")
        last = _last_in_turn(store).checkpoint
    # Eight turns add 64 KB of history; the execution part carries their IDs only.
    assert max(sizes) - min(sizes) < 4_000
    requests = _requests(last.execution)
    assert len(requests) == 3
    assert all(isinstance(slot, str) for slot in requests[-1].messages[:16])
    restored = SessionCheckpoint.from_parts(
        SessionState.from_json(last.state.to_json()),
        SessionExecution.from_json(last.execution.to_json()),
    )
    assert restored == last


async def test_requests_changed_by_a_hook_keep_the_messages_the_model_received():
    class Redact(Plugin):
        @hook(PrepareRequest)
        async def redact(self, event, next_):
            request = await next_()
            first, *rest = request.messages
            return replace(request, messages=(replace(first, content="[redacted]"), *rest))

    store = SplitStore()
    model = ScriptedModel(["one", "two"])
    async with Agent(model=model, plugins=[Redact()]).session(on_commit=store) as session:
        await session.run("secret")
        await session.run("again")
        last = _last_in_turn(store).checkpoint
    (request,) = _requests(last.execution)
    redacted, answer, question = request.messages
    # The changed history message is stored in full, the unchanged one by ID. The
    # open turn's input is not in the state yet, so its first request carries it.
    assert not isinstance(redacted, str) and redacted.text == "[redacted]"
    assert isinstance(answer, str)
    assert not isinstance(question, str) and question.text == "again"
    restored = SessionCheckpoint.from_parts(last.state, last.execution)
    (recorded,) = [event for event in restored.events if isinstance(event, ModelRequestedEvent)]
    assert recorded.request.messages == model.requests[1].messages
    assert restored == last


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
    store = SplitStore()
    async with Agent(model=model, plugins=[UpdateReplay()]).session(on_commit=store) as session:
        await session.run("one")
        await session.run("two")
    last = _last_in_turn(store).checkpoint
    restored = SessionCheckpoint.from_parts(
        SessionState.from_json(last.state.to_json()),
        SessionExecution.from_json(last.execution.to_json()),
    )
    (event,) = [event for event in restored.events if isinstance(event, ModelRequestedEvent)]
    (reply,) = [
        message for message in event.request.messages if isinstance(message, AssistantMessage)
    ]
    flag = reply.provider_state.data["flag"]
    assert type(flag) is int and flag == 1


async def test_an_execution_referring_to_an_unknown_message_is_rejected():
    store = SplitStore()
    async with Agent(model=ScriptedModel(["one", "two"])).session(on_commit=store) as session:
        await session.run("first")
        await session.run("second")
        last = _last_in_turn(store).checkpoint
    document = last.execution.to_dict()
    (request,) = [entry for entry in document["events"] if entry["type"] == "checkpoint.request"]
    request["messages"][0] = "missing"
    with pytest.raises(SessionFormatError, match="does not contain"):
        SessionCheckpoint.from_parts(last.state, SessionExecution.from_dict(document))
