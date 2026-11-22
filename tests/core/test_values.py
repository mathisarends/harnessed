from collections.abc import Hashable

import pytest
from pydantic import ValidationError

from harnessed.events import ModelRequestedEvent, ObservationRecordedEvent, TurnStartedEvent
from harnessed.models import (
    AssistantMessage,
    ImagePart,
    ModelInfo,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    TextPart,
    ToolCall,
    ToolDefinition,
    UserMessage,
)


@pytest.mark.parametrize(
    "value",
    [
        ToolCall("read", {"path": "file"}),
        UserMessage("hello"),
        ToolDefinition("read", "Read", {"type": "object"}),
        ModelRequest(()),
        ModelResponse(AssistantMessage("done")),
        TurnStartedEvent("one"),
        ModelRequestedEvent(ModelRequest(()), request_id="request", model=ModelInfo("test")),
    ],
)
def test_mapping_values_and_log_records_are_explicitly_unhashable(value):
    assert not isinstance(value, Hashable)
    with pytest.raises(TypeError):
        hash(value)


def test_observations_reject_mutable_capture_buffers():
    parts = [TextPart("page"), ImagePart("https://example.test/screen.png")]
    with pytest.raises(ValidationError):
        UserMessage(parts)
    message = UserMessage(tuple(parts))
    with pytest.raises(ValidationError):
        ObservationRecordedEvent(message, "browser", "turn", 1, ["frame"])
    snapshot = ObservationRecordedEvent(message, "browser", "turn", 1, ("frame",))
    parts.clear()
    assert snapshot.message.content == (
        TextPart("page"),
        ImagePart("https://example.test/screen.png"),
    )
    assert snapshot.refs == ("frame",)


@pytest.mark.parametrize("content", [None, 42, ("plain text is not a content part",)])
def test_observation_rejects_unsupported_content(content):
    with pytest.raises(ValidationError):
        UserMessage(content)


def test_scalar_values_remain_hashable_and_payload_equality_remains_structural():
    assert hash(ModelInfo("test")) == hash(ModelInfo("test"))
    assert hash(ModelUsage(12, 3)) == hash(ModelUsage(12, 3))
    assert ToolCall("read", {"a": 1, "b": 2}, "one") == ToolCall("read", {"b": 2, "a": 1}, "one")
