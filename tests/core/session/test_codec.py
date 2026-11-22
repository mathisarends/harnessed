import json
from pathlib import Path

import pytest

from harnessed.errors import SessionFormatError
from harnessed.events import ModelRequestedEvent, ToolCallRequestedEvent, TurnStartedEvent
from harnessed.events.codec import builtin_codec
from harnessed.events.registry import EventRegistry
from harnessed.events.unions import SESSION_EVENT_TYPES
from harnessed.models import ModelInfo, ModelRequest, ToolCall, UserMessage


def test_canonical_tags_roundtrip_with_discriminated_schema():
    assert set(EventRegistry().schema()["discriminator"]["mapping"]) == set(SESSION_EVENT_TYPES)
    event = TurnStartedEvent("one")
    assert builtin_codec().encode(event) == {
        "version": 9,
        "type": "turn.started",
        "data": {
            "turn_id": "one",
            "input_id": None,
            "settings": {"max_tokens": None, "temperature": None, "provider_options": {}},
            "output_schema": None,
            "limits": {"max_steps": None, "token_budget": None},
            "final_step": "none",
        },
    }
    assert builtin_codec().decode(builtin_codec().encode(event)) == event


def test_version_three_audit_records_are_rejected():
    record = builtin_codec().encode(TurnStartedEvent("one"))
    record["version"] = 3
    with pytest.raises(SessionFormatError, match="Unsupported session record version"):
        builtin_codec().decode(record)


def test_model_request_fixture_uses_the_canonical_codec():
    path = Path(__file__).with_name("fixtures") / "model_requests.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events = [builtin_codec().decode(record) for record in records]
    assert len(events) == 7
    assert isinstance(events[2], ModelRequestedEvent)
    assert events[2].request_id == "request"
    assert events[2].request.messages[0].text == "hello"
    assert [builtin_codec().encode(event)["type"] for event in events] == [
        r["type"] for r in records
    ]


@pytest.mark.parametrize(
    "record",
    [
        {"type": "turn.started", "data": {"turn_id": "one"}},
        {"version": True, "type": "turn.started", "data": {"turn_id": "one"}},
        {"version": 1, "type": "turn.started", "data": {"turn_id": "one"}},
        {"version": 2, "type": "turn.started", "data": {"turn_id": "one"}},
        {"version": 9, "type": "turn/start", "data": {"turn_id": "one"}},
        {"version": 9, "type": "TurnStartedEvent", "data": {"turn_id": "one"}},
        {"version": 9, "type": "turn.started", "data": {"turn_id": "one", "extra": 1}},
        {"version": 9, "type": "turn.started", "data": {"turn_id": ""}},
        {"version": 9, "type": "step.started", "data": {"turn_id": "one", "step": True}},
        {"version": 9, "type": "step.started", "data": {"turn_id": "one", "step": 0}},
        {"version": 9, "type": "turn.ended", "data": {"turn_id": "one", "reason": "typo"}},
        {"version": 9, "type": "turn.started", "seq": False, "data": {"turn_id": "one"}},
        {"version": 9, "type": "turn.started", "data": {"turn_id": "one", "type": "turn.ended"}},
    ],
)
def test_invalid_records_fail_at_the_codec_boundary(record):
    with pytest.raises(SessionFormatError):
        builtin_codec().decode(record)


def test_nested_values_reject_non_json_keys_and_obsolete_fields():
    record = builtin_codec().encode(ToolCallRequestedEvent(ToolCall("read")))
    record["data"]["call"]["arguments"] = {1: "not a JSON key"}
    with pytest.raises(SessionFormatError):
        builtin_codec().decode(record)
    record = builtin_codec().encode(
        ModelRequestedEvent(
            ModelRequest((UserMessage("hello"),)), request_id="request", model=ModelInfo("test")
        )
    )
    record["data"]["request"]["cache_breakpoints"] = [1]
    with pytest.raises(SessionFormatError):
        builtin_codec().decode(record)


@pytest.mark.parametrize("role", ["user", "system", "tool"])
def test_messages_reject_fields_from_other_variants(role):
    payload = {"role": role, "content": "text", "tool_calls": [{"name": "unsafe"}]}
    if role == "tool":
        payload["tool_call_id"] = "call"
    record = {
        "version": 9,
        "type": "model.requested",
        "data": {"request": {"model": "test", "messages": [payload]}},
    }
    with pytest.raises(SessionFormatError):
        builtin_codec().decode(record)
