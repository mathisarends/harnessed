from dataclasses import field
from enum import StrEnum
from typing import Literal

from pydantic.dataclasses import dataclass

from harnessed.models import ModelUsage, UserMessage
from harnessed.models.values import VALUE_CONFIG

from .base import DurableEvent, Identifier, StepNumber
from .child_status import ChildRuntimeState
from .kinds import EventKind


class SubagentStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    LIMITED = "limited"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, config=VALUE_CONFIG)
class SubagentLinkedEvent(DurableEvent):
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


@dataclass(frozen=True, config=VALUE_CONFIG)
class SubagentStartedEvent(DurableEvent):
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


@dataclass(frozen=True, config=VALUE_CONFIG)
class SubagentUpdatedEvent(DurableEvent):
    """A bounded runtime projection, including explicitly requested status reports."""

    type: Literal[EventKind.SUBAGENT_UPDATED] = field(
        default=EventKind.SUBAGENT_UPDATED, kw_only=True
    )
    child_session_id: Identifier
    runtime: ChildRuntimeState


@dataclass(frozen=True, config=VALUE_CONFIG)
class SubagentMessageSentEvent(DurableEvent):
    type: Literal[EventKind.SUBAGENT_MESSAGE_SENT] = field(
        default=EventKind.SUBAGENT_MESSAGE_SENT, kw_only=True
    )
    child_session_id: Identifier
    message: UserMessage


@dataclass(frozen=True, config=VALUE_CONFIG)
class SubagentFinishedEvent(DurableEvent):
    """A child receipt; usage includes its descendants, never parent model usage."""

    type: Literal[EventKind.SUBAGENT_FINISHED] = field(
        default=EventKind.SUBAGENT_FINISHED, kw_only=True
    )
    child_session_id: Identifier
    status: SubagentStatus
    text: str = ""
    usage: ModelUsage = field(default_factory=lambda: ModelUsage(0))

    def __post_init__(self) -> None:
        if self.status is SubagentStatus.RUNNING:
            raise ValueError("A finished subagent requires a terminal status")


@dataclass(frozen=True, config=VALUE_CONFIG)
class SubagentApprovalRequestedEvent(DurableEvent):
    """Correlate a parent approval with the original request in a child log."""

    type: Literal[EventKind.SUBAGENT_APPROVAL_REQUESTED] = field(
        default=EventKind.SUBAGENT_APPROVAL_REQUESTED, kw_only=True
    )
    child_session_id: Identifier
    child_request_id: Identifier
    call_id: Identifier
