from typing import Any

from harnessed.core.diagnostics import ModelAttemptOutcome, RetryKind, RetryReason
from harnessed.core.events.approvals import (
    ApprovalNeededEvent,
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
    ApprovalStatus,
    PolicySource,
)
from harnessed.core.events.base import CustomTransientEvent as TransientEvent
from harnessed.core.events.base import DurableEvent, SessionEvent
from harnessed.core.events.compaction import (
    CompactionAppliedEvent,
    CompactionFinishedEvent,
    CompactionOutcome,
    CompactionStartedEvent,
)
from harnessed.core.events.completion import TurnFinishedEvent
from harnessed.core.events.inbox import (
    InboxAddedEvent,
    InboxMode,
    InboxRemovalReason,
    InboxRemovedEvent,
    InboxTarget,
)
from harnessed.core.events.kinds import EventKind
from harnessed.core.events.messages import (
    AssistantMessageEvent,
    ContextLifetime,
    ContextMessageEvent,
    ContextValueChangedEvent,
    ReasoningDeltaEvent,
    SystemMessageEvent,
    TextDeltaEvent,
    UserMessageEvent,
)
from harnessed.core.events.model import (
    CacheMetricsEvent,
    ModelAttemptFinishedEvent,
    ModelAttemptStartedEvent,
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelRequestOutcome,
    ModelRetryScheduledEvent,
    ModelUsageRecordedEvent,
)
from harnessed.core.events.observations import (
    IncludedObservation,
    ObservationPolicyAppliedEvent,
    ObservationRecordedEvent,
    ObservationsIncludedEvent,
    SpillCreatedEvent,
    SpillExpirationReason,
    SpillExpiredEvent,
)
from harnessed.core.events.output import OutputValidatedEvent, OutputValidationFailedEvent
from harnessed.core.events.registry import EventRegistry
from harnessed.core.events.rules import RuleAction, RuleTriggeredEvent
from harnessed.core.events.session import (
    AgentControlChangedEvent,
    ControlState,
    SessionStatusChangedEvent,
)
from harnessed.core.events.stream import EventStream, StreamEvent
from harnessed.core.events.subagents import (
    SubagentApprovalRequestedEvent,
    SubagentFinishedEvent,
    SubagentLinkedEvent,
    SubagentMessageSentEvent,
    SubagentStartedEvent,
    SubagentStatus,
    SubagentUpdatedEvent,
)
from harnessed.core.events.tools import (
    ToolArgumentsDeltaEvent,
    ToolCallFinishedEvent,
    ToolCallGenerationStartedEvent,
    ToolCallRequestedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolPolicyAppliedEvent,
    ToolProgressEvent,
)
from harnessed.core.events.turn import (
    StepEndedEvent,
    StepOutcome,
    StepStartedEvent,
    TurnEndedEvent,
    TurnOutcome,
    TurnStartedEvent,
)
from harnessed.core.events.unions import LogEvent
from harnessed.core.model import (
    ToolArgumentsMode,
    UsagePurpose,
)

type BuiltinEvent = LogEvent | StreamEvent[Any]

__all__ = [
    "AgentControlChangedEvent",
    "ApprovalNeededEvent",
    "ApprovalRequestedEvent",
    "ApprovalResolvedEvent",
    "ApprovalRevokedEvent",
    "ApprovalStatus",
    "AssistantMessageEvent",
    "BuiltinEvent",
    "CacheMetricsEvent",
    "CompactionAppliedEvent",
    "CompactionFinishedEvent",
    "CompactionOutcome",
    "CompactionStartedEvent",
    "ContextLifetime",
    "ContextMessageEvent",
    "ContextValueChangedEvent",
    "ControlState",
    "DurableEvent",
    "EventKind",
    "EventRegistry",
    "EventStream",
    "InboxAddedEvent",
    "InboxMode",
    "InboxRemovalReason",
    "InboxRemovedEvent",
    "InboxTarget",
    "IncludedObservation",
    "ModelAttemptFinishedEvent",
    "ModelAttemptOutcome",
    "ModelAttemptStartedEvent",
    "ModelRequestFailedEvent",
    "ModelRequestOutcome",
    "ModelRequestedEvent",
    "ModelRetryScheduledEvent",
    "ModelUsageRecordedEvent",
    "ObservationPolicyAppliedEvent",
    "ObservationRecordedEvent",
    "ObservationsIncludedEvent",
    "OutputValidatedEvent",
    "OutputValidationFailedEvent",
    "PolicySource",
    "ReasoningDeltaEvent",
    "RetryKind",
    "RetryReason",
    "RuleAction",
    "RuleTriggeredEvent",
    "SessionEvent",
    "SessionStatusChangedEvent",
    "SpillCreatedEvent",
    "SpillExpirationReason",
    "SpillExpiredEvent",
    "StepEndedEvent",
    "StepOutcome",
    "StepStartedEvent",
    "StreamEvent",
    "SubagentApprovalRequestedEvent",
    "SubagentFinishedEvent",
    "SubagentLinkedEvent",
    "SubagentMessageSentEvent",
    "SubagentStartedEvent",
    "SubagentStatus",
    "SubagentUpdatedEvent",
    "SystemMessageEvent",
    "TextDeltaEvent",
    "ToolArgumentsDeltaEvent",
    "ToolArgumentsMode",
    "ToolCallFinishedEvent",
    "ToolCallGenerationStartedEvent",
    "ToolCallRequestedEvent",
    "ToolExecutionFinishedEvent",
    "ToolExecutionStartedEvent",
    "ToolPolicyAppliedEvent",
    "ToolProgressEvent",
    "TransientEvent",
    "TurnEndedEvent",
    "TurnFinishedEvent",
    "TurnOutcome",
    "TurnStartedEvent",
    "UsagePurpose",
    "UserMessageEvent",
]
