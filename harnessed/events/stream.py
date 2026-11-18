from dataclasses import fields, replace
from typing import Annotated, get_args, get_origin

from pydantic import Field

from harnessed.models import ToolArgumentsMode

from .approvals import ApprovalNeededEvent
from .base import DurableEvent, TransientEvent
from .compaction import (
    CompactionAppliedEvent,
    CompactionFinishedEvent,
    CompactionStartedEvent,
)
from .completion import TurnResultEvent
from .messages import ReasoningDeltaEvent, TextDeltaEvent
from .model import (
    ModelAttemptFinishedEvent,
    ModelAttemptStartedEvent,
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelRetryScheduledEvent,
)
from .observations import SpillCreatedEvent, SpillExpiredEvent
from .session import SessionStatusChangedEvent
from .subagents import (
    SubagentApprovalRequestedEvent,
    SubagentFinishedEvent,
    SubagentLinkedEvent,
    SubagentMessageSentEvent,
    SubagentStartedEvent,
    SubagentUpdatedEvent,
)
from .tools import (
    ToolArgumentsDeltaEvent,
    ToolCallFinishedEvent,
    ToolCallGenerationStartedEvent,
    ToolCallRequestedEvent,
    ToolExecutionStartedEvent,
    ToolProgressEvent,
)

type StreamEvent[T] = Annotated[
    (
        TextDeltaEvent
        | ReasoningDeltaEvent
        | ToolCallGenerationStartedEvent
        | ToolArgumentsDeltaEvent
        | ToolCallRequestedEvent
        | ToolExecutionStartedEvent
        | ToolProgressEvent
        | ToolCallFinishedEvent
        | ApprovalNeededEvent
        | SessionStatusChangedEvent
        | TurnResultEvent[T]
        | CompactionAppliedEvent
        | CompactionStartedEvent
        | CompactionFinishedEvent
        | ModelAttemptStartedEvent
        | ModelAttemptFinishedEvent
        | ModelRetryScheduledEvent
        | ModelRequestedEvent
        | ModelRequestFailedEvent
        | SpillCreatedEvent
        | SpillExpiredEvent
        | SubagentLinkedEvent
        | SubagentStartedEvent
        | SubagentFinishedEvent
        | SubagentApprovalRequestedEvent
        | SubagentUpdatedEvent
        | SubagentMessageSentEvent
    ),
    Field(discriminator="type"),
]


STREAM_EVENT_TYPES: dict[str, type] = {
    str(next(f.default for f in fields(kind) if f.name == "type")): kind
    for item in get_args(get_args(StreamEvent.__value__)[0])
    for kind in (get_origin(item) or item,)
}


type StreamItem[T] = StreamEvent[T] | DurableEvent | TransientEvent[T]


def coalesce_deltas[E](previous: E, event: E) -> E | None:
    """Fold adjacent deltas of the same message into one, or return None.

    Text and reasoning fragments concatenate. Argument APPEND fragments of the
    same call concatenate and keep the newer name and call ID; a REPLACE snapshot
    supersedes any earlier fragment of its call. Everything else stays separate.
    """
    if (isinstance(previous, TextDeltaEvent) and isinstance(event, TextDeltaEvent)) or (
        isinstance(previous, ReasoningDeltaEvent) and isinstance(event, ReasoningDeltaEvent)
    ):
        if previous.message_id == event.message_id:
            return replace(event, text=previous.text + event.text)
    elif isinstance(previous, ToolArgumentsDeltaEvent) and isinstance(
        event, ToolArgumentsDeltaEvent
    ):
        before, after = previous.delta, event.delta
        if previous.message_id != event.message_id or before.index != after.index:
            return None
        if after.mode is ToolArgumentsMode.REPLACE:
            return event
        if before.mode is ToolArgumentsMode.APPEND:
            merged = replace(after, arguments=before.arguments + after.arguments)
            return replace(event, delta=merged)
    return None
