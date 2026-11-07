import asyncio
import json
from dataclasses import FrozenInstanceError, dataclass, field, replace
from typing import Literal

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from harnessed import Agent, Plugin, SessionState, ToolContext, tool
from harnessed.errors import SessionFormatError
from harnessed.events import (
    ControlState,
    DurableEvent,
    EventRegistry,
    SessionControlChangedEvent,
    TurnStartedEvent,
    UserMessageEvent,
)
from harnessed.integrations.json import JsonSessionStore
from harnessed.kernel import Inject
from harnessed.models import UserMessage
from harnessed.sessions import CheckpointExtension, SessionCheckpoint, StoredSession
from harnessed.testing import ScriptedModel, call
from tests._checkpoints import checkpoint


@dataclass(frozen=True)
class IndexedEvent(DurableEvent):
    files: int
    type: Literal["project.indexed"] = field(default="project.indexed", kw_only=True)


class BackendEnvelope(BaseModel):
    checkpoint: SessionCheckpoint


def test_checkpoint_has_typed_messages_and_nested_pydantic_roundtrip():
    value = replace(
        checkpoint(),
        control=SessionControlChangedEvent(ControlState.PAUSED),
        events=(TurnStartedEvent("turn"), UserMessageEvent(UserMessage("hello", id="user-id"))),
    )
    envelope = BackendEnvelope(checkpoint=value)
    restored = BackendEnvelope.model_validate_json(envelope.model_dump_json()).checkpoint
    assert restored == value
    message = restored.events[1]
    assert isinstance(message, UserMessageEvent)
    assert message.message.id == "user-id" and message.message.text == "hello"
    assert restored.control.state is ControlState.PAUSED
    schema = BackendEnvelope.model_json_schema()
    assert schema["$defs"]["SessionCheckpoint"]["properties"]["version"]["const"] == 3
    assert "UserMessageEvent" in schema["$defs"]


def test_checkpoint_extensions_are_immutable_and_roundtrip_without_a_registry():
    extension = CheckpointExtension(tag="project.details", data={"items": [{"name": "a"}]})
    value = replace(checkpoint(), events=(TurnStartedEvent("turn"), extension))
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
        value.offset = 3
    with pytest.raises(TypeError):
        CheckpointExtension(tag="project.details", data={"invalid": float("inf")})


@pytest.mark.parametrize(
    "change", ["old-version", "bool-version", "extra", "offset", "seed", "end"]
)
def test_checkpoint_rejects_invalid_documents(change):
    data = json.loads(checkpoint().to_json())
    if change == "old-version":
        data["version"] = 1
    elif change == "bool-version":
        data["version"] = True
    elif change == "extra":
        data["unknown"] = "ignored?"
    elif change == "offset":
        data["offset"] = -1
    elif change == "seed":
        data["seed"] = [{"type": "turn.started", "turn_id": "turn"}]
    else:
        data["events"] = [
            {"type": "turn.started", "turn_id": "turn"},
            {"type": "turn.ended", "turn_id": "turn"},
        ]
    with pytest.raises(SessionFormatError):
        SessionCheckpoint.from_json(json.dumps(data))


def test_checkpoint_construction_and_type_adapter_validate_without_json():
    with pytest.raises(ValidationError):
        SessionCheckpoint(
            state=SessionState(), offset=-1, control=SessionControlChangedEvent("active")
        )
    with pytest.raises(SessionFormatError):
        SessionCheckpoint.from_json("{}")
    value = checkpoint("typed")
    assert TypeAdapter(SessionCheckpoint).validate_python(value) is value
    assert value.state.plugin_instructions == {"marker": "typed"}
    with pytest.raises(TypeError, match="SessionCheckpoint"):
        StoredSession("{}", 1)


async def test_custom_event_restoration_uses_the_agent_catalog_not_the_store(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    class Indexer(Plugin):
        events = (IndexedEvent,)

        @tool(readonly=True)
        async def index(self, *, ctx: Inject[ToolContext]) -> str:
            await ctx.emit(IndexedEvent(42))
            entered.set()
            await release.wait()
            return "indexed"

    store = JsonSessionStore(root=tmp_path)
    model = ScriptedModel([call(Indexer.index), "finished"])
    agent = Agent(model=model, plugins=[Indexer()], store=store)
    async with agent.session(id="custom") as session:
        await session.submit("Index")
        await asyncio.wait_for(entered.wait(), timeout=5)
        pausing = asyncio.create_task(session.pause())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(pausing, timeout=5)

    stored = await JsonSessionStore(root=tmp_path).load_session("custom")
    assert stored is not None
    extension = next(
        event for event in stored.checkpoint.events if isinstance(event, CheckpointExtension)
    )
    assert extension.tag == "project.indexed" and extension.data == {"files": 42}
    with pytest.raises(SessionFormatError, match="Invalid checkpoint execution"):
        async with Agent(model=model, store=store).session(id="custom"):
            pytest.fail("An unregistered extension must not run")
    with pytest.raises(SessionFormatError):
        replace(extension, data={"files": "invalid"}).to_event(
            registry=EventRegistry([IndexedEvent])
        )

    async with agent.session(id="custom") as restored:
        assert (
            next(event for event in restored.events if isinstance(event, IndexedEvent)).files == 42
        )
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert restored.messages[-1].text == "finished"
    assert len(model.requests) == 2
