from dataclasses import field
from enum import StrEnum
from typing import Literal

from pydantic.dataclasses import dataclass

from harnessed.models import ApprovalResponse, ApprovalSource, Decision, ToolCall
from harnessed.models.values import VALUE_CONFIG

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


@dataclass(frozen=True, config=VALUE_CONFIG)
class ApprovalRequestedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.APPROVAL_REQUESTED] = field(
        default=EventKind.APPROVAL_REQUESTED, kw_only=True
    )
    request_id: Identifier
    call: ToolCall
    reason: str
    scope: Identifier
    grant_id: str | None = None
    identity: str | None = None

    def allow(self, *, remember: bool = False) -> ApprovalResponse:
        return ApprovalResponse(Decision.ALLOW, remember=remember)

    def deny(self) -> Decision:
        return Decision.DENY


@dataclass(frozen=True, config=VALUE_CONFIG)
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
    source: ApprovalSource | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.remember and self.decision is not Decision.ALLOW:
            raise ValueError("Only allow decisions can be remembered")
        if self.remember and self.source is ApprovalSource.REVIEWER:
            raise ValueError("Reviewer approval applies to one request")
        if self.status == ApprovalStatus.ANSWERED and self.decision in (
            Decision.ALLOW,
            Decision.DENY,
        ):
            return
        if (
            self.status in (ApprovalStatus.INTERRUPTED, ApprovalStatus.UNAVAILABLE)
            and self.decision is None
            and self.source is None
        ):
            return
        raise ValueError("Approval must be answered, interrupted or unavailable")


@dataclass(frozen=True, config=VALUE_CONFIG)
class ApprovalRevokedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.APPROVAL_REVOKED] = field(
        default=EventKind.APPROVAL_REVOKED, kw_only=True
    )
    request_id: Identifier
    reason: str = "revoked"


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class ApprovalNeededEvent(TransientEvent):
    type: Literal[EventKind.APPROVAL_NEEDED] = field(
        default=EventKind.APPROVAL_NEEDED, kw_only=True
    )
    request_id: str
    call: ToolCall
    reason: str
