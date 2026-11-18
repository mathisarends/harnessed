from typing import Annotated

from pydantic import Field

from .base import DurableEvent, TransientEvent
from .compaction import CompactionAppliedEvent
from .completion import TurnResultEvent as TurnResultEvent
from .live import LiveEvent
from .model import ModelRequestedEvent, ModelRequestFailedEvent
from .observations import SpillCreatedEvent, SpillExpiredEvent
from .subagents import (
    SubagentApprovalRequestedEvent,
    SubagentFinishedEvent,
    SubagentLinkedEvent,
    SubagentMessageSentEvent,
    SubagentStartedEvent,
    SubagentUpdatedEvent,
)
from .tools import (
    ToolCallFinishedEvent,
    ToolCallRequestedEvent,
    ToolExecutionStartedEvent,
    ToolProgressEvent,
)

type StreamEvent[T] = Annotated[
    (
        LiveEvent[T]
        | ToolCallRequestedEvent
        | ToolExecutionStartedEvent
        | ToolProgressEvent
        | ToolCallFinishedEvent
        | CompactionAppliedEvent
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


type StreamItem[T] = StreamEvent[T] | DurableEvent | TransientEvent[T]
