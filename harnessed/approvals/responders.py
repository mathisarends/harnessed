from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from harnessed.events import ApprovalRequestedEvent
from harnessed.kernel import Key
from harnessed.models import ApprovalResponse, Decision

if TYPE_CHECKING:
    from harnessed.sessions import HistoryEntry


@dataclass(frozen=True, slots=True)
class ApprovalReviewRequest:
    """The pending approval and a snapshot of the session's retained history."""

    approval: ApprovalRequestedEvent
    history: tuple[HistoryEntry, ...]


@runtime_checkable
class ApprovalReviewer(Protocol):
    async def review(self, request: ApprovalReviewRequest) -> ApprovalResponse | None:
        """Answer this request once, or return None to defer to another responder."""
        ...


@runtime_checkable
class Approver(Protocol):
    async def ask(self, request: ApprovalRequestedEvent) -> ApprovalResponse | Decision | None:
        """Return an approval answer, or None when this responder cannot answer."""
        ...


type ApprovalCallback = Callable[
    [ApprovalRequestedEvent],
    ApprovalResponse
    | Decision
    | bool
    | Awaitable[ApprovalResponse | Decision | bool | None]
    | None,
]


class CallbackApprover:
    """Adapt a request callback to the narrow Approver service."""

    def __init__(self, callback: ApprovalCallback) -> None:
        self.callback = callback

    async def ask(self, request: ApprovalRequestedEvent) -> ApprovalResponse | Decision | None:
        value = self.callback(request)
        value = await value if inspect.isawaitable(value) else value
        if value is None or isinstance(value, ApprovalResponse | Decision):
            return value
        if type(value) is bool:
            return Decision.ALLOW if value else Decision.DENY
        raise TypeError("Approvers must return an approval response, Decision, bool or None")


PARENT_APPROVER = Key[Approver]("parent approver")
