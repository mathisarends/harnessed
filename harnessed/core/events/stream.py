from dataclasses import replace
from typing import Annotated

from pydantic import Field

from harnessed.core.events.approvals import ApprovalNeededEvent
from harnessed.core.events.base import CustomTransientEvent, DurableEvent
from harnessed.core.events.compaction import (
    CompactionAppliedEvent,
    CompactionFinishedEvent,
    CompactionStartedEvent,
)
from harnessed.core.events.completion import TurnFinishedEvent
from harnessed.core.events.messages import ReasoningDeltaEvent, TextDeltaEvent
from harnessed.core.events.model import (
    ModelAttemptFinishedEvent,
    ModelAttemptStartedEvent,
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelRetryScheduledEvent,
)
from harnessed.core.events.observations import SpillCreatedEvent, SpillExpiredEvent
from harnessed.core.events.session import SessionStatusChangedEvent
from harnessed.core.events.subagents import (
    SubagentApprovalRequestedEvent,
    SubagentFinishedEvent,
    SubagentLinkedEvent,
    SubagentMessageSentEvent,
    SubagentStartedEvent,
    SubagentUpdatedEvent,
)
from harnessed.core.events.tools import (
    ToolArgumentsDeltaEvent,
    ToolCallFinishedEvent,
    ToolCallGenerationStartedEvent,
    ToolCallRequestedEvent,
    ToolProgressEvent,
)
from harnessed.core.model import ToolArgumentsMode

type StreamEvent[T] = Annotated[
    (
        TextDeltaEvent
        | ReasoningDeltaEvent
        | ToolCallGenerationStartedEvent
        | ToolArgumentsDeltaEvent
        | ToolCallRequestedEvent
        | ToolProgressEvent
        | ToolCallFinishedEvent
        | ApprovalNeededEvent
        | SessionStatusChangedEvent
        | TurnFinishedEvent[T]
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


type EventStream[T] = StreamEvent[T] | DurableEvent | CustomTransientEvent


def coalesce_deltas[E](previous: E, event: E) -> E | None:
    """Fold adjacent deltas of the same message into one, or return None.

    Text and reasoning fragments concatenate. Argument APPEND fragments of the
    same call concatenate and keep the newer name and call ID; a REPLACE snapshot
    supersedes any earlier fragment of its call. Everything else stays separate.
    """
    if isinstance(previous, TextDeltaEvent) and isinstance(event, TextDeltaEvent):
        if previous.message_id == event.message_id:
            return replace(event, text=previous.text + event.text)
    elif isinstance(previous, ReasoningDeltaEvent) and isinstance(event, ReasoningDeltaEvent):
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
