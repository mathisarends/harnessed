from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from harnessed.core.child_status import ChildRuntimeState
from harnessed.core.events.base import Identifier, SessionEvent, StepNumber
from harnessed.core.events.kinds import EventKind
from harnessed.core.messages import UserMessage
from harnessed.core.model import ModelUsage


class SubagentStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    LIMITED = "limited"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class SubagentLinkedEvent(SessionEvent):
    """A child's immutable origin; recorded before any child work starts."""

    type: Literal[EventKind.SUBAGENT_LINKED] = field(
        default=EventKind.SUBAGENT_LINKED, kw_only=True
    )
    parent_session_id: Identifier
    parent_turn_id: Identifier
    parent_call_id: Identifier
    agent: Identifier
    depth: StepNumber
    depth_limit: StepNumber


@dataclass(frozen=True)
class SubagentStartedEvent(SessionEvent):
    type: Literal[EventKind.SUBAGENT_STARTED] = field(
        default=EventKind.SUBAGENT_STARTED, kw_only=True
    )
    child_session_id: Identifier
    agent: Identifier
    task: str
    turn_id: Identifier
    call_id: Identifier
    depth: StepNumber
    background: bool = False


@dataclass(frozen=True)
class SubagentUpdatedEvent(SessionEvent):
    """A bounded runtime projection, including explicitly requested status reports."""

    type: Literal[EventKind.SUBAGENT_UPDATED] = field(
        default=EventKind.SUBAGENT_UPDATED, kw_only=True
    )
    child_session_id: Identifier
    runtime: ChildRuntimeState


@dataclass(frozen=True)
class SubagentMessageSentEvent(SessionEvent):
    type: Literal[EventKind.SUBAGENT_MESSAGE_SENT] = field(
        default=EventKind.SUBAGENT_MESSAGE_SENT, kw_only=True
    )
    child_session_id: Identifier
    message: UserMessage


@dataclass(frozen=True)
class SubagentFinishedEvent(SessionEvent):
    """A child receipt; usage includes its descendants, never parent model usage."""

    type: Literal[EventKind.SUBAGENT_FINISHED] = field(
        default=EventKind.SUBAGENT_FINISHED, kw_only=True
    )
    child_session_id: Identifier
    status: SubagentStatus
    text: str = ""
    usage: ModelUsage = field(default_factory=lambda: ModelUsage(0))

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", SubagentStatus(self.status))
        if self.status is SubagentStatus.RUNNING:
            raise ValueError("A finished subagent requires a terminal status")


@dataclass(frozen=True)
class SubagentApprovalRequestedEvent(SessionEvent):
    """Correlate a parent approval with the original request in a child log."""

    type: Literal[EventKind.SUBAGENT_APPROVAL_REQUESTED] = field(
        default=EventKind.SUBAGENT_APPROVAL_REQUESTED, kw_only=True
    )
    child_session_id: Identifier
    child_request_id: Identifier
    call_id: Identifier
