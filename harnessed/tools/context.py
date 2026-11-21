from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from harnessed.events import DurableEvent, ToolProgressEvent, TransientEvent
from harnessed.models import Decision, ToolCall

if TYPE_CHECKING:
    from harnessed.approvals.session import SessionApprovals
    from harnessed.runtime.inbox import SessionInbox
    from harnessed.sessions.log import SessionLog


@dataclass
class ToolContext:
    """Per-call services without access to the runtime driver."""

    call: ToolCall
    log: SessionLog
    inbox: SessionInbox
    approvals: SessionApprovals
    signal: asyncio.Event = field(default_factory=asyncio.Event)

    approval_identity: str | None = None

    @property
    def cancelled(self) -> bool:
        return self.signal.is_set()

    async def progress(self, text: str) -> None:
        await self.log.append(ToolProgressEvent(self.call.id, text))

    async def emit(self, event: DurableEvent | TransientEvent) -> None:
        await self.log.emit(event)

    async def approve(
        self, reason: str, *, scope: str = "tool-call", timeout: float | None = None
    ) -> bool:
        """Ask a human to approve a step of this call; True for allow or always.

        Uses the session's approval flow: a durable request, an existing ``ALWAYS``
        grant for this tool and scope, the configured approver or a stream
        responder. Raises ``ApprovalUnavailable`` when nobody can answer.
        ``timeout=None`` inherits ``Approvals(timeout=...)``; a finite positive
        value replaces that response timeout for this question. Approval waits
        do not consume the tool's execution timeout.
        """
        answer = await self.approvals.request(
            self.call, reason, scope=scope, timeout=timeout, identity=self.approval_identity
        )
        return answer is Decision.ALLOW


class ToolContexts(Protocol):
    def create(self, call: ToolCall) -> ToolContext:
        """Bind the call to its session log, inbox and approval services."""
        ...


@dataclass(frozen=True)
class BoundToolContexts:
    log: SessionLog
    inbox: SessionInbox
    approvals: SessionApprovals

    def create(self, call: ToolCall) -> ToolContext:
        return ToolContext(call, self.log, self.inbox, self.approvals)
