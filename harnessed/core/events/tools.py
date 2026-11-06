from dataclasses import dataclass, field
from typing import Literal

from harnessed.core.events.approvals import PolicySource
from harnessed.core.events.base import Identifier, SessionEvent, TransientEvent
from harnessed.core.events.kinds import EventKind
from harnessed.core.messages import PolicyDecision, ToolCall, ToolResult
from harnessed.core.model import ToolCallDelta, ToolCallStart


@dataclass(frozen=True)
class ToolPolicyAppliedEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.TOOL_POLICY_APPLIED] = field(
        default=EventKind.TOOL_POLICY_APPLIED, kw_only=True
    )
    call_id: Identifier
    decision: PolicyDecision
    source: PolicySource = PolicySource.POLICY

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", PolicySource(self.source))
        object.__setattr__(self, "decision", PolicyDecision(self.decision))
        if self.decision is PolicyDecision.ASK:
            raise ValueError("ToolPolicyAppliedEvent requires a final decision")


@dataclass(frozen=True)
class ToolExecutionStartedEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.TOOL_EXECUTION_STARTED] = field(
        default=EventKind.TOOL_EXECUTION_STARTED, kw_only=True
    )
    call_id: Identifier


@dataclass(frozen=True)
class ToolExecutionFinishedEvent(SessionEvent):
    """The unreviewed execution receipt, saved before ordered result review.

    Text respects the captured output limit. This is audit data, not a
    model-visible result. Recovery must never bypass ReviewToolResult by
    publishing this receipt as ToolCallFinishedEvent.
    """

    __hash__ = None
    type: Literal[EventKind.TOOL_EXECUTION_FINISHED] = field(
        default=EventKind.TOOL_EXECUTION_FINISHED, kw_only=True
    )
    call_id: Identifier
    result: ToolResult


@dataclass(frozen=True)
class ToolCallRequestedEvent(SessionEvent):
    """A complete call accepted for scheduling, before policy or execution."""

    __hash__ = None

    type: Literal[EventKind.TOOL_CALL_REQUESTED] = field(
        default=EventKind.TOOL_CALL_REQUESTED, kw_only=True
    )
    call: ToolCall

    @property
    def name(self) -> str:
        return self.call.name


@dataclass(frozen=True)
class ToolCallFinishedEvent(SessionEvent):
    """The final reviewed and limited result, committed in model call order.

    Shared by the durable log and UI stream, including calls denied before
    execution and synthetic recovery errors. Raw execution receipts are separate.
    """

    __hash__ = None

    type: Literal[EventKind.TOOL_CALL_FINISHED] = field(
        default=EventKind.TOOL_CALL_FINISHED, kw_only=True
    )
    call: ToolCall
    result: ToolResult

    def __post_init__(self) -> None:
        if not isinstance(self.call, ToolCall):
            raise TypeError("ToolCallFinishedEvent requires a ToolCall")

    @property
    def call_id(self) -> str:
        return self.call.id


@dataclass(frozen=True)
class ToolProgressEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.TOOL_PROGRESS] = field(default=EventKind.TOOL_PROGRESS, kw_only=True)
    call_id: Identifier
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallGenerationStartedEvent(TransientEvent):
    """The model began generating a call preview; no tool has been scheduled."""

    type: Literal[EventKind.TOOL_CALL_GENERATION_STARTED] = field(
        default=EventKind.TOOL_CALL_GENERATION_STARTED, kw_only=True
    )
    message_id: str
    call: ToolCallStart


@dataclass(frozen=True, slots=True)
class ToolArgumentsDeltaEvent(TransientEvent):
    type: Literal[EventKind.TOOL_ARGUMENTS_DELTA] = field(
        default=EventKind.TOOL_ARGUMENTS_DELTA, kw_only=True
    )
    message_id: str
    delta: ToolCallDelta
