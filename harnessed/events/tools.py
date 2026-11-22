from dataclasses import field
from typing import Literal

from pydantic.dataclasses import dataclass

from harnessed.models import Decision, ToolCall, ToolCallDelta, ToolCallStart, ToolResult
from harnessed.models.values import VALUE_CONFIG

from .approvals import PolicySource
from .base import DurableEvent, Identifier, TransientEvent
from .kinds import EventKind


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolPolicyAppliedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.TOOL_POLICY_APPLIED] = field(
        default=EventKind.TOOL_POLICY_APPLIED, kw_only=True
    )
    call_id: Identifier
    decision: Decision
    source: PolicySource = PolicySource.POLICY

    def __post_init__(self) -> None:
        if self.decision is Decision.ASK:
            raise ValueError("ToolPolicyAppliedEvent requires a final decision")


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolExecutionStartedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.TOOL_EXECUTION_STARTED] = field(
        default=EventKind.TOOL_EXECUTION_STARTED, kw_only=True
    )
    call_id: Identifier


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolExecutionFinishedEvent(DurableEvent):
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


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolCallRequestedEvent(DurableEvent):
    """A complete call accepted for scheduling, before policy or execution."""

    __hash__ = None

    type: Literal[EventKind.TOOL_CALL_REQUESTED] = field(
        default=EventKind.TOOL_CALL_REQUESTED, kw_only=True
    )
    call: ToolCall

    @property
    def name(self) -> str:
        return self.call.name


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolCallFinishedEvent(DurableEvent):
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

    @property
    def call_id(self) -> str:
        return self.call.id


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolProgressEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.TOOL_PROGRESS] = field(default=EventKind.TOOL_PROGRESS, kw_only=True)
    call_id: Identifier
    text: str


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class ToolCallGenerationStartedEvent(TransientEvent):
    """The model began generating a call preview; no tool has been scheduled."""

    type: Literal[EventKind.TOOL_CALL_GENERATION_STARTED] = field(
        default=EventKind.TOOL_CALL_GENERATION_STARTED, kw_only=True
    )
    message_id: str
    call: ToolCallStart


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class ToolArgumentsDeltaEvent(TransientEvent):
    type: Literal[EventKind.TOOL_ARGUMENTS_DELTA] = field(
        default=EventKind.TOOL_ARGUMENTS_DELTA, kw_only=True
    )
    message_id: str
    delta: ToolCallDelta
