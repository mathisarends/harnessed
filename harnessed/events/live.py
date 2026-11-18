from dataclasses import fields
from typing import get_args, get_origin

from .approvals import ApprovalNeededEvent
from .compaction import CompactionFinishedEvent, CompactionStartedEvent
from .completion import TurnResultEvent
from .messages import ReasoningDeltaEvent, TextDeltaEvent
from .model import ModelAttemptFinishedEvent, ModelAttemptStartedEvent, ModelRetryScheduledEvent
from .session import SessionStatusChangedEvent
from .tools import ToolArgumentsDeltaEvent, ToolCallGenerationStartedEvent

type LiveEvent[T] = (
    TextDeltaEvent
    | ReasoningDeltaEvent
    | ToolCallGenerationStartedEvent
    | ToolArgumentsDeltaEvent
    | ApprovalNeededEvent
    | SessionStatusChangedEvent
    | TurnResultEvent[T]
    | CompactionStartedEvent
    | CompactionFinishedEvent
    | ModelAttemptStartedEvent
    | ModelAttemptFinishedEvent
    | ModelRetryScheduledEvent
)


def _live_event_types() -> dict[str, type]:
    events: dict[str, type] = {}
    for item in get_args(LiveEvent.__value__):
        kind = get_origin(item) or item
        tag = next(field.default for field in fields(kind) if field.name == "type")
        events[str(tag)] = kind
    return events


LIVE_EVENT_TYPES = _live_event_types()
