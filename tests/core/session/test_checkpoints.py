import asyncio
import json
from dataclasses import FrozenInstanceError, dataclass, field, replace
from typing import Literal

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from harnessed import Agent, Plugin, ToolContext, tool
from harnessed.errors import SessionFormatError
from harnessed.events import (
    ControlState,
    DurableEvent,
    SessionControlChangedEvent,
    TurnStartedEvent,
    UserMessageEvent,
)
from harnessed.events.registry import EventRegistry
from harnessed.kernel import Inject
from harnessed.models import ImageReference, ModelChunk, UserMessage
from harnessed.sessions import (
    CheckpointExtension,
    JournalEntry,
    SessionBoundary,
    SessionCheckpoint,
    SessionState,
)
from harnessed.testing import CallbackModel, ScriptedModel, call
from tests._checkpoints import boundary, checkpoint
from tests._commits import CommitRecorder


@dataclass(frozen=True)
class IndexedEvent(DurableEvent):
    files: int
    type: Literal["project.indexed"] = field(default="project.indexed", kw_only=True)


class BackendEnvelope(BaseModel):
    checkpoint: SessionCheckpoint


def _with_journal(*events) -> SessionCheckpoint:
    return SessionCheckpoint(
        boundary(),
        tuple(JournalEntry(sequence, event) for sequence, event in enumerate(events)),
    )


def test_checkpoint_has_typed_messages_and_nested_pydantic_roundtrip():
    value = SessionCheckpoint(
        replace(boundary(), control=SessionControlChangedEvent(ControlState.PAUSED)),
        _with_journal(
            TurnStartedEvent("turn"), UserMessageEvent(UserMessage("hello", id="user-id"))
        ).journal,
    )
    envelope = BackendEnvelope(checkpoint=value)
    restored = BackendEnvelope.model_validate_json(envelope.model_dump_json()).checkpoint
    assert restored == value
    message = restored.events[1]
    assert isinstance(message, UserMessageEvent)
    assert message.message.id == "user-id" and message.message.text == "hello"
    assert restored.boundary.control.state is ControlState.PAUSED
    schema = BackendEnvelope.model_json_schema()["$defs"]
    assert schema["SessionCheckpoint"]["properties"]["version"]["const"] == 6
    assert schema["SessionBoundary"]["properties"]["version"]["const"] == 1
    assert schema["JournalEntry"]["properties"]["version"]["const"] == 1
    assert "UserMessageEvent" in schema


def test_boundaries_and_entries_roundtrip_as_separate_documents():
    value = _with_journal(TurnStartedEvent("turn"), UserMessageEvent(UserMessage("hello")))
    stored_boundary = SessionBoundary.from_json(value.boundary.to_json())
    stored_entries = [JournalEntry.from_dict(entry.to_dict()) for entry in value.journal]
    assert SessionCheckpoint.from_journal(stored_boundary, stored_entries) == value
    with pytest.raises(SessionFormatError):
        SessionBoundary.from_dict({"version": 2})
    with pytest.raises(SessionFormatError):
        JournalEntry.from_dict({"sequence": -1, "record": {"type": "turn.started"}})


def test_checkpoint_extensions_are_immutable_and_roundtrip_without_a_registry():
    extension = CheckpointExtension(tag="project.details", data={"items": [{"name": "a"}]})
    value = _with_journal(TurnStartedEvent("turn"), extension)
    restored = SessionCheckpoint.from_json(value.to_json())
    assert restored == value
    assert (
        BackendEnvelope.model_validate_json(
            BackendEnvelope(checkpoint=value).model_dump_json()
        ).checkpoint
        == value
    )
    with pytest.raises(TypeError):
        extension.data["changed"] = True
    with pytest.raises(FrozenInstanceError):
        value.journal = ()
    with pytest.raises(ValidationError):
        CheckpointExtension(tag="project.details", data={"invalid": float("inf")})


@pytest.mark.parametrize(
    "change",
    ["old-version", "bool-version", "extra", "sequence", "seed", "end", "gap", "id", "blank-id"],
)
def test_checkpoint_rejects_invalid_documents(change):
    data = json.loads(_with_journal(TurnStartedEvent("turn")).to_json())
    if change == "old-version":
        data["version"] = 5
    elif change == "bool-version":
        data["version"] = True
    elif change == "extra":
        data["unknown"] = "ignored?"
    elif change == "sequence":
        data["boundary"]["sequence"] = -1
    elif change == "id":
        del data["boundary"]["session_id"]
    elif change == "blank-id":
        data["boundary"]["session_id"] = " "
    elif change == "seed":
        data["boundary"]["seed"] = [{"type": "turn.started", "turn_id": "turn"}]
    elif change == "gap":
        data["journal"][0]["sequence"] = 1
    else:
        data["journal"].append({"sequence": 1, "record": {"type": "turn.ended", "turn_id": "turn"}})
    with pytest.raises(SessionFormatError):
        SessionCheckpoint.from_json(json.dumps(data))


def test_checkpoint_construction_and_type_adapter_validate_without_json():
    with pytest.raises(ValidationError):
        SessionBoundary(
            session_id="session",
            sequence=-1,
            state=SessionState(),
            control=SessionControlChangedEvent(ControlState.ACTIVE),
        )
    with pytest.raises(SessionFormatError):
        SessionCheckpoint.from_json("{}")
    value = checkpoint("typed")
    assert TypeAdapter(SessionCheckpoint).validate_python(value) is value
    assert value.state.plugin_values == {"marker": "typed"}
    assert SessionCheckpoint.from_dict(value.to_dict()) == value


async def test_custom_event_restoration_uses_the_agent_catalog_not_the_document():
    entered, release = asyncio.Event(), asyncio.Event()

    class Indexer(Plugin):
        events = (IndexedEvent,)

        @tool(readonly=True)
        async def index(self, *, ctx: Inject[ToolContext]) -> str:
            await ctx.emit(IndexedEvent(42))
            entered.set()
            await release.wait()
            return "indexed"

    store = CommitRecorder(documents=True)
    model = ScriptedModel([call(Indexer.index), "finished"])
    agent = Agent(model=model, plugins=[Indexer()])
    async with agent.session(id="custom", on_commit=store) as session:
        await session.submit("Index")
        await asyncio.wait_for(entered.wait(), timeout=5)
        pausing = asyncio.create_task(session.pause())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(pausing, timeout=5)

    stored = store.latest("custom")
    extension = next(event for event in stored.events if isinstance(event, CheckpointExtension))
    assert extension.tag == "project.indexed" and extension.data == {"files": 42}
    with pytest.raises(SessionFormatError, match="Invalid checkpoint execution"):
        async with Agent(model=model).session(state=stored):
            pytest.fail("An unregistered extension must not run")
    with pytest.raises(SessionFormatError):
        replace(extension, data={"files": "invalid"}).to_event(
            registry=EventRegistry([IndexedEvent])
        )

    async with agent.session(state=stored) as restored:
        assert (
            next(event for event in restored.events if isinstance(event, IndexedEvent)).files == 42
        )
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert restored.messages[-1].text == "finished"
    assert len(model.requests) == 2


async def _image_checkpoints() -> tuple[SessionCheckpoint, SessionCheckpoint]:
    """A terminal checkpoint with a retained image and one whose open turn adds one."""
    started, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        if len(request.messages) > 2:
            started.set()
            await release.wait()
        yield ModelChunk(text="seen")

    async with Agent(model=CallbackModel(respond)).session() as session:
        kept = await session.assets.put(b"\x00\xffkept", media_type="image/png")
        await session.run(UserMessage((ImageReference(kept),)))
        terminal = session.checkpoint()
        added = await session.assets.put(b"\x00\xffadded", media_type="image/png")
        await session.submit(UserMessage((ImageReference(added),)))
        await asyncio.wait_for(started.wait(), timeout=5)
        running = session.checkpoint()
        release.set()
    return terminal, running


@pytest.mark.parametrize("encoding", ["dict", "json"])
async def test_decoding_a_checkpoint_with_tampered_image_bytes_fails(encoding):
    terminal, running = await _image_checkpoints()
    assert running.assets and terminal.state.assets
    # The open turn's image was stored before the turn began, so its boundary holds it.
    for document, assets in ((terminal.to_dict(), "state"), (running.to_dict(), "boundary")):
        if assets == "state":
            target = document["boundary"]["state"]["assets"]
        else:
            target = document["boundary"]["assets"]
        target[0]["data"] = "Y29ycnVwdA=="
        with pytest.raises(SessionFormatError):
            if encoding == "dict":
                SessionCheckpoint.from_dict(document)
            else:
                SessionCheckpoint.from_json(json.dumps(document))
