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


LIVE_EVENT_TYPES: dict[str, type] = {
    str(next(field.default for field in fields(kind) if field.name == "type")): kind
    for item in get_args(LiveEvent.__value__)
    for kind in (get_origin(item) or item,)
}
