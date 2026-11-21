from typing import Annotated, get_args

from pydantic import Field

from .approvals import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
)
from .base import DurableEvent
from .compaction import CompactionAppliedEvent
from .inbox import InboxAddedEvent, InboxRemovedEvent
from .messages import (
    AssistantMessageEvent,
    MessageAppendedEvent,
    PluginValueChangedEvent,
    SystemMessageEvent,
    UserMessageEvent,
)
from .model import (
    ModelCacheMetricsEvent,
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelUsageRecordedEvent,
)
from .observations import (
    ObservationPolicyAppliedEvent,
    ObservationRecordedEvent,
    ObservationsIncludedEvent,
    SpillCreatedEvent,
    SpillExpiredEvent,
)
from .output import OutputValidatedEvent, OutputValidationFailedEvent
from .session import SessionControlChangedEvent
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
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolPolicyAppliedEvent,
    ToolProgressEvent,
)
from .turn import (
    StepEndedEvent,
    StepStartedEvent,
    TurnEndedEvent,
    TurnStartedEvent,
)

type LogEvent = Annotated[
    (
        SessionControlChangedEvent
        | ApprovalRequestedEvent
        | ApprovalResolvedEvent
        | ApprovalRevokedEvent
        | ToolPolicyAppliedEvent
        | ToolExecutionStartedEvent
        | ToolExecutionFinishedEvent
        | InboxAddedEvent
        | InboxRemovedEvent
        | TurnStartedEvent
        | StepStartedEvent
        | SystemMessageEvent
        | UserMessageEvent
        | MessageAppendedEvent
        | PluginValueChangedEvent
        | ObservationRecordedEvent
        | ObservationsIncludedEvent
        | ModelRequestedEvent
        | ModelRequestFailedEvent
        | ModelUsageRecordedEvent
        | ModelCacheMetricsEvent
        | CompactionAppliedEvent
        | ObservationPolicyAppliedEvent
        | SpillCreatedEvent
        | SpillExpiredEvent
        | AssistantMessageEvent
        | ToolCallRequestedEvent
        | ToolCallFinishedEvent
        | ToolProgressEvent
        | StepEndedEvent
        | TurnEndedEvent
        | OutputValidatedEvent
        | OutputValidationFailedEvent
        | SubagentLinkedEvent
        | SubagentStartedEvent
        | SubagentFinishedEvent
        | SubagentApprovalRequestedEvent
        | SubagentUpdatedEvent
        | SubagentMessageSentEvent
    ),
    Field(discriminator="type"),
]


SESSION_EVENT_TYPES: dict[str, type[DurableEvent]] = {
    kind.type: kind for kind in get_args(get_args(LogEvent.__value__)[0])
}
