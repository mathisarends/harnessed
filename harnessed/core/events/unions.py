from typing import Annotated, get_args

from pydantic import Field

from harnessed.core.events.approvals import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
)
from harnessed.core.events.base import SessionEvent
from harnessed.core.events.compaction import CompactionAppliedEvent
from harnessed.core.events.inbox import InboxAddedEvent, InboxRemovedEvent
from harnessed.core.events.messages import (
    AssistantMessageEvent,
    ContextMessageEvent,
    ContextValueChangedEvent,
    SystemMessageEvent,
    UserMessageEvent,
)
from harnessed.core.events.model import (
    CacheMetricsEvent,
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelUsageRecordedEvent,
)
from harnessed.core.events.observations import (
    ObservationPolicyAppliedEvent,
    ObservationRecordedEvent,
    ObservationsIncludedEvent,
    SpillCreatedEvent,
    SpillExpiredEvent,
)
from harnessed.core.events.output import OutputValidatedEvent, OutputValidationFailedEvent
from harnessed.core.events.rules import RuleTriggeredEvent
from harnessed.core.events.session import AgentControlChangedEvent
from harnessed.core.events.subagents import (
    SubagentApprovalRequestedEvent,
    SubagentFinishedEvent,
    SubagentLinkedEvent,
    SubagentMessageSentEvent,
    SubagentStartedEvent,
    SubagentUpdatedEvent,
)
from harnessed.core.events.tools import (
    ToolCallFinishedEvent,
    ToolCallRequestedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolPolicyAppliedEvent,
    ToolProgressEvent,
)
from harnessed.core.events.turn import (
    StepEndedEvent,
    StepStartedEvent,
    TurnEndedEvent,
    TurnStartedEvent,
)

type LogEvent = Annotated[
    (
        AgentControlChangedEvent
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
        | ContextMessageEvent
        | ContextValueChangedEvent
        | ObservationRecordedEvent
        | ObservationsIncludedEvent
        | ModelRequestedEvent
        | ModelRequestFailedEvent
        | ModelUsageRecordedEvent
        | CacheMetricsEvent
        | CompactionAppliedEvent
        | ObservationPolicyAppliedEvent
        | SpillCreatedEvent
        | SpillExpiredEvent
        | RuleTriggeredEvent
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


SESSION_EVENT_TYPES: dict[str, type[SessionEvent]] = {
    kind.type: kind for kind in get_args(get_args(LogEvent.__value__)[0])
}
