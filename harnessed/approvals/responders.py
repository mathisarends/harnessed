import inspect
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from harnessed.events import ApprovalRequestedEvent
from harnessed.kernel import Key
from harnessed.models import Decision
from harnessed.models.messages import ApprovalResponse


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
