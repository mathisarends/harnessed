import asyncio
import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, TypeAdapter

from harnessed import Agent, Plugin, tool
from harnessed.errors import SessionFormatError
from harnessed.events import ModelRequestedEvent, SessionStatus
from harnessed.models import ToolMessage
from harnessed.sessions import JournalExtension, SessionRecovery, StoredSession
from harnessed.sessions.upcasting import DocumentFormat
from harnessed.testing import ScriptedModel
from tests._commits import CommitRecorder

_FIXTURES = Path(__file__).with_name("fixtures")
_CHECKPOINT = _FIXTURES / "paused-checkpoint-v5.json"
_RECOVERY = _FIXTURES / "recovery-v6.json"


def test_document_reader_applies_each_successive_step_and_validates_the_result():
    class Current(BaseModel):
        version: Literal[3]
        message: str

    def first(document):
        assert document["version"] == 1
        return {"text": document["content"]}

    def second(document):
        assert document["version"] == 2
        return {"message": document["text"]}

    reader = DocumentFormat("example", 1, (first, second))
    adapter = TypeAdapter(Current)
    result = reader.decode(adapter, '{"version":1,"content":"hello"}')
    assert result == Current(version=3, message="hello")
    assert reader.decode(adapter, '{"version":2,"text":"hello"}') == result
    assert reader.decode(adapter, result.model_dump_json()) == result
    with pytest.raises(SessionFormatError):
        reader.decode(adapter, '{"version":1,"content":[]}')


def test_frozen_v6_recovery_matches_separately_stored_documents():
    payload = _RECOVERY.read_bytes()
    raw = json.loads(payload)
    separate = StoredSession(raw["snapshot"], raw["journal"])
    value = separate.recovery()
    assert value.to_dict() == raw
    assert SessionRecovery.from_json(payload) == value
    assert value.state.history[0].message.text == "Conversation baseline"


@pytest.mark.parametrize("version", [0, 2, True, "1", 1.0])
def test_embedded_journal_versions_use_their_own_window(version):
    raw = json.loads(_RECOVERY.read_bytes())
    raw["journal"][0]["version"] = version
    with pytest.raises(SessionFormatError):
        SessionRecovery.from_dict(raw)
    with pytest.raises(SessionFormatError):
        StoredSession(raw["snapshot"], raw["journal"]).recovery()


def test_old_extension_payload_is_opaque_and_offline_conversion_is_repeatable():
    raw = json.loads(_CHECKPOINT.read_bytes())
    data = {"version": 100, "type": "checkpoint.extension"}
    raw["events"].append({"type": "checkpoint.extension", "tag": "work.receipt", "data": data})
    original = json.dumps(raw)
    value = SessionRecovery.from_dict(raw)
    assert value.events[-1] == JournalExtension("work.receipt", data)
    assert SessionRecovery.from_json(value.to_json()) == value
    assert json.dumps(raw) == original
    assert value.version == 6


def test_checkpoint_final_control_takes_effect_after_its_recorded_transitions():
    raw = json.loads(_CHECKPOINT.read_bytes())
    raw["control"]["state"] = "active"
    value = SessionRecovery.from_dict(raw)
    assert value.journal[-2].to_dict()["record"]["state"] == "paused"
    assert value.journal[-1].to_dict()["record"] == raw["control"]


async def test_reopen_released_paused_checkpoint_keeps_the_completed_tool_result():
    invocations = []

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            invocations.append("work")
            return "replayed"

    old = json.loads(_CHECKPOINT.read_bytes())
    value = SessionRecovery.from_json(_CHECKPOINT.read_bytes())
    assert [e.sequence for e in value.journal] == list(range(old["offset"], old["offset"] + 16))
    request = next(event for event in value.events if isinstance(event, ModelRequestedEvent))
    assert request.request.messages[-1].text == "Work"
    store = CommitRecorder(documents=True)
    store.put(value)
    model = ScriptedModel(["finished"])
    async with Agent(model=model, plugins=[Work()]).session(
        id="fixture-paused", store=store
    ) as session:
        assert session.status is SessionStatus.PAUSED
        await session.resume()
        await asyncio.wait_for(session.wait_until_idle(), timeout=5)
        assert session.messages[-1].text == "finished"
    assert invocations == []
    assert len(model.requests) == 1
    results = [m for m in model.requests[0].messages if isinstance(m, ToolMessage)]
    assert [(m.tool_call_id, m.text) for m in results] == [
        ("995703c1b4f24af580dab11849cc83db", "persisted tool result")
    ]
