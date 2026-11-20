from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from harnessed.models import Decision, ToolCall
from harnessed.models.messages import ApprovalResponse

from .base import DurableEvent, Identifier, TransientEvent
from .kinds import EventKind


class ApprovalStatus(StrEnum):
    ANSWERED = "answered"
    INTERRUPTED = "interrupted"
    UNAVAILABLE = "unavailable"


class PolicySource(StrEnum):
    POLICY = "policy"
    APPROVAL = "approval"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ApprovalRequestedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.APPROVAL_REQUESTED] = field(
        default=EventKind.APPROVAL_REQUESTED, kw_only=True
    )
    request_id: Identifier
    call: ToolCall
    reason: str
    scope: str
    grant_id: str | None = None
    identity: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.request_id, str)
            or not self.request_id
            or not isinstance(self.scope, str)
            or not self.scope
        ):
            raise ValueError("Approvals require a request_id and scope")
        if not isinstance(self.call, ToolCall):
            raise TypeError("Approvals require a ToolCall")

    def allow(self, *, remember: bool = False) -> ApprovalResponse:
        return ApprovalResponse(Decision.ALLOW, remember=remember)

    def deny(self) -> Decision:
        return Decision.DENY


@dataclass(frozen=True)
class ApprovalResolvedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.APPROVAL_RESOLVED] = field(
        default=EventKind.APPROVAL_RESOLVED, kw_only=True
    )
    request_id: Identifier
    decision: Decision | None = None
    remember: bool = field(default=False, kw_only=True)
    status: ApprovalStatus = ApprovalStatus.ANSWERED
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ApprovalStatus(self.status))
        if self.decision is not None:
            object.__setattr__(self, "decision", Decision(self.decision))
        if type(self.remember) is not bool or (
            self.remember and self.decision is not Decision.ALLOW
        ):
            raise ValueError("Only allow decisions can be remembered")
        if self.reason is not None and not isinstance(self.reason, str):
            raise TypeError("Approval resolution reason must be a string")
        if self.status == ApprovalStatus.ANSWERED and self.decision in (
            Decision.ALLOW,
            Decision.DENY,
        ):
            return
        if (
            self.status in (ApprovalStatus.INTERRUPTED, ApprovalStatus.UNAVAILABLE)
            and self.decision is None
        ):
            return
        raise ValueError("Approval must be answered, interrupted or unavailable")


@dataclass(frozen=True)
class ApprovalRevokedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.APPROVAL_REVOKED] = field(
        default=EventKind.APPROVAL_REVOKED, kw_only=True
    )
    request_id: Identifier
    reason: str = "revoked"


@dataclass(frozen=True, slots=True)
class ApprovalNeededEvent(TransientEvent):
    type: Literal[EventKind.APPROVAL_NEEDED] = field(
        default=EventKind.APPROVAL_NEEDED, kw_only=True
    )
    request_id: str
    call: ToolCall
    reason: str
