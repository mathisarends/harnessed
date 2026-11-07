from typing import Self

from harnessed.core.errors import ApprovalUnavailable
from harnessed.core.events.approvals import (
    ApprovalNeededEvent,
    ApprovalResolvedEvent,
    ApprovalStatus,
)
from harnessed.core.events.inbox import InboxAddedEvent
from harnessed.core.events.turn import TurnStartedEvent
from harnessed.core.hooks import RequestApproval, SessionAppended
from harnessed.core.messages import ApprovalAnswer
from harnessed.core.runtime.approvals import LiveApproval, LiveApprovals
from harnessed.core.session.session import SessionLog
from harnessed.kernel import Priority, Scope


class StreamApprovals:
    """Only the stream owning this inbox item can answer its turn's approvals."""

    def __init__(self, scope: Scope, log: SessionLog, live: LiveApprovals) -> None:
        self.scope = scope
        self.log = log
        self.live = live
        self.input_id: str | None = None
        self.pending: dict[str, LiveApproval] = {}

    async def __aenter__(self) -> Self:
        self._remove_answer = self.scope.on(
            RequestApproval, self._answer, priority=Priority.OBSERVE
        )
        self._remove_ack = self.scope.on(SessionAppended, self._ack)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._remove_answer()
        await self._remove_ack()
        for request in self.pending.values():
            self.live.close(
                request.request_id, ApprovalUnavailable("The approval stream was closed")
            )
        self.pending.clear()

    def prepared(self, item: InboxAddedEvent) -> None:
        self.input_id = item.message.id

    async def _answer(self, event: RequestApproval) -> ApprovalAnswer | None:
        turn = self.log.open_turn
        start = turn.events[0] if turn is not None else None
        if (
            event.session.log is not self.log
            or not isinstance(start, TurnStartedEvent)
            or self.input_id is None
            or start.input_id != self.input_id
        ):
            return None
        request = self.live.open(event.request)
        self.pending[request.request_id] = request
        try:
            await self.log.publish(
                ApprovalNeededEvent(request.request_id, request.call, request.reason)
            )
            return await request.decision()
        except BaseException:
            self.live.close(
                request.request_id,
                ApprovalUnavailable("The approval wait ended before acknowledgement"),
            )
            raise

    def _ack(self, event: SessionAppended) -> None:
        if event.log is not self.log or not isinstance(event.event, ApprovalResolvedEvent):
            return
        record = event.event
        request = self.pending.pop(record.request_id, None)
        if request is not None:
            self.live.close(
                request.request_id,
                None
                if record.status == ApprovalStatus.ANSWERED
                else ApprovalUnavailable(record.reason or "The approval was interrupted"),
            )
