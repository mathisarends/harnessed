from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Self
from uuid import uuid4

from harnessed.errors import ApprovalUnavailable
from harnessed.events import (
    ApprovalNeededEvent,
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
    ApprovalStatus,
    TurnStartedEvent,
)
from harnessed.kernel import Priority, Scope
from harnessed.models import ApprovalResponse, ApprovalSource, Decision, ToolCall
from harnessed.runtime.control import SessionControl
from harnessed.runtime.hooks import RequestApproval, SessionAppended
from harnessed.sessions import SessionContext
from harnessed.sessions.log import SessionLog
from harnessed.tools import suspend_tool_execution
from harnessed.tracing import span

from .policy import DEFAULT_APPROVALS, Approval, ApprovalsConfig, validate_approval_timeout
from .responders import PARENT_APPROVER, ApprovalReviewer, ApprovalReviewRequest, Approver

logger = logging.getLogger(__name__)


class LiveApproval:
    """A live approval handle; answer() waits for its decision's in-memory admission."""

    def __init__(self, request: ApprovalRequestedEvent) -> None:
        self.request_id = request.request_id
        self.call = request.call
        self.reason = request.reason
        loop = asyncio.get_running_loop()
        self._decision: asyncio.Future[ApprovalResponse | None] = loop.create_future()
        self._ack: asyncio.Future[Exception | None] = loop.create_future()

    async def answer(self, decision: ApprovalResponse) -> None:
        if not isinstance(decision, ApprovalResponse):
            raise TypeError("Approval answers must be an allow or deny response")
        if self._decision.done():
            if self._decision.cancelled() or self._decision.result() is None:
                raise ApprovalUnavailable("This approval is no longer pending")
            if self._decision.result() != decision:
                raise ValueError("An approval cannot change its submitted decision")
        else:
            self._decision.set_result(decision)
        failure = await asyncio.shield(self._ack)
        if failure is not None:
            raise failure

    async def decision(self) -> ApprovalResponse | None:
        """Wait for an answer; None means the approval closed without one."""
        return await self._decision

    def close(self, failure: Exception | None = None) -> None:
        if not self._decision.done():
            self._decision.set_result(None)
        if not self._ack.done():
            self._ack.set_result(failure)


class LiveApprovals:
    """Approvals a live responder can answer, shared by the session and its responders.

    It also records which inputs an open stream owns and which session-scoped
    responders are attached, so a closing stream can hand its questions over.
    """

    def __init__(self) -> None:
        self._items: dict[str, LiveApproval] = {}
        self._owned: dict[str, int] = {}
        self._responders: list[LiveResponder] = []

    def own(self, input_id: str) -> None:
        self._owned[input_id] = self._owned.get(input_id, 0) + 1

    def release(self, input_id: str) -> None:
        remaining = self._owned.pop(input_id, 0) - 1
        if remaining > 0:
            self._owned[input_id] = remaining

    def owns(self, input_id: str) -> bool:
        """Whether an open stream answers the turn started by this input."""
        return input_id in self._owned

    def attach(self, responder: LiveResponder) -> None:
        self._responders.append(responder)

    def detach(self, responder: LiveResponder) -> None:
        self._responders.remove(responder)

    def successor(self, leaving: LiveResponder) -> LiveResponder | None:
        """The most recently attached session responder other than the leaving one."""
        return next(
            (responder for responder in reversed(self._responders) if responder is not leaving),
            None,
        )

    def open(self, request: ApprovalRequestedEvent) -> LiveApproval:
        approval = LiveApproval(request)
        self._items[request.request_id] = approval
        return approval

    def get(self, request_id: str) -> LiveApproval | None:
        return self._items.get(request_id)

    def close(self, request_id: str, failure: Exception | None = None) -> None:
        approval = self._items.pop(request_id, None)
        if approval is not None:
            approval.close(failure)


class LiveResponder:
    """Announce approval requests and hold them until an answer is acknowledged.

    Subclasses decide which requests they answer; ``_ack`` settles a held
    request once its durable resolution is recorded.
    """

    def __init__(self, scope: Scope, log: SessionLog, live: LiveApprovals) -> None:
        self.scope = scope
        self.log = log
        self._live = live
        self.pending: dict[str, LiveApproval] = {}

    async def __aenter__(self) -> Self:
        self._remove_answer = self.scope.on(
            RequestApproval, self._answer, priority=Priority.OBSERVE
        )
        self._remove_ack = self.scope.on(SessionAppended, self._ack)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._remove_answer()
        await self._remove_ack()

    def adopt(self, request: LiveApproval) -> None:
        """Hold a request that another responder announced and can no longer answer."""
        self.pending[request.request_id] = request

    def open_turn_input(self) -> str | None:
        turn = self.log.open_turn
        start = turn.events[0] if turn is not None else None
        return start.input_id if isinstance(start, TurnStartedEvent) else None

    def answers(self) -> bool:
        """Whether this responder holds the open turn's requests; placeholder for subclasses."""
        raise NotImplementedError

    def hand_over(self, message: str) -> None:
        """Pass held requests to an attached session responder, or close them."""
        successor = self._live.successor(self)
        for request in self.pending.values():
            if successor is not None:
                successor.adopt(request)
            else:
                self._live.close(request.request_id, ApprovalUnavailable(message))
        self.pending.clear()

    async def _answer(self, event: RequestApproval) -> ApprovalResponse | None:
        if not self.answers():
            return None
        request = self._live.open(event.request)
        self.pending[request.request_id] = request
        try:
            await self.log.publish(
                ApprovalNeededEvent(request.request_id, request.call, request.reason)
            )
            return await request.decision()
        except BaseException:
            self._live.close(
                request.request_id,
                ApprovalUnavailable("The approval wait ended before acknowledgement"),
            )
            raise

    def _ack(self, event: SessionAppended) -> None:
        if not isinstance(event.event, ApprovalResolvedEvent):
            return
        record = event.event
        request = self.pending.pop(record.request_id, None)
        if request is not None:
            self._live.close(
                request.request_id,
                None
                if record.status == ApprovalStatus.ANSWERED
                else ApprovalUnavailable(record.reason or "The approval was interrupted"),
            )


_NO_RESPONDER = "No approval responder answered the request"


class SessionResponder(LiveResponder):
    """Hold the session's approval requests that no open stream owns.

    It answers requests of turns started by ``steer()``, ``submit()`` or a
    subagent notification, and requests asked while no turn is open.
    """

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self._live.attach(self)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._live.detach(self)
        await super().__aexit__(*exc)
        self.hand_over("The session approval responder was closed")

    def answers(self) -> bool:
        input_id = self.open_turn_input()
        return input_id is None or not self._live.owns(input_id)


class SessionApprovals:
    """Approval snapshots and acknowledged responses addressed by request ID."""

    def __init__(
        self,
        scope: Scope,
        log: SessionLog,
        control: SessionControl,
        live: LiveApprovals,
    ) -> None:
        self.scope = scope
        self.log = log
        self.control = control
        self._live = live

    @property
    def records(self) -> tuple[Approval, ...]:
        return self.log.approvals.records

    @property
    def pending(self) -> tuple[Approval, ...]:
        return self.log.approvals.pending

    @property
    def grants(self) -> tuple[Approval, ...]:
        return self.log.approvals.grants

    def grant_for(self, tool: str, *, scope: str) -> Approval | None:
        return self.log.approvals.grant_for(tool, scope=scope)

    @asynccontextmanager
    async def responder(self) -> AsyncGenerator[None]:
        """Keep approval requests answerable while this context is open.

        Requests of turns that no ``session.stream()`` owns stay pending, are
        announced as ``ApprovalNeededEvent`` on ``session.subscribe(transient=True)``
        and wait for ``allow()`` or ``deny()`` until their timeout. A stream that
        closes hands its pending requests over to an open responder.
        """
        self.control.check_open()
        async with SessionResponder(self.scope, self.log, self._live):
            yield

    async def allow(self, request_id: str, *, remember: bool = False) -> None:
        """Approve this request, optionally retaining its grant for the current session."""
        await self._answer(request_id, ApprovalResponse(Decision.ALLOW, remember=remember))

    async def deny(self, request_id: str) -> None:
        """Decline this request and acknowledge its durable resolution."""
        await self._answer(request_id, ApprovalResponse(Decision.DENY))

    async def request(
        self,
        call: ToolCall,
        reason: str,
        *,
        scope: str = "tool-policy",
        identity: str | None = None,
        approver: Approver | None = None,
        timeout_seconds: float | None = None,
    ) -> Decision:
        """Ask for approval using a response timeout in seconds.

        ``None`` inherits ``Approvals(timeout_seconds=...)``. A finite positive override
        replaces that default for this question, even when it is longer.
        Missing responders and expired questions raise ``ApprovalUnavailable``.
        """
        response = await self.request_response(
            call,
            reason,
            scope=scope,
            identity=identity,
            approver=approver,
            timeout_seconds=timeout_seconds,
        )
        return response.decision

    async def request_response(
        self,
        call: ToolCall,
        reason: str,
        *,
        scope: str = "tool-policy",
        identity: str | None = None,
        approver: Approver | None = None,
        timeout_seconds: float | None = None,
    ) -> ApprovalResponse:
        """Use the same approval lifecycle as request(), returning the full answer."""
        self.control.check_open()
        if timeout_seconds is None:
            timeout_seconds = self.scope.get(ApprovalsConfig, DEFAULT_APPROVALS).timeout_seconds
        validate_approval_timeout(timeout_seconds)
        grant = self.log.approval_grant(
            identity if identity is not None else call.name, scope=scope
        )
        request = ApprovalRequestedEvent(
            uuid4().hex, call, reason, scope, grant.request_id if grant else None, identity
        )
        await self.log.append(request)
        try:
            if grant is not None:
                decision = ApprovalResponse(Decision.ALLOW, remember=True)
            else:
                decision = await self._answer_request(
                    request, approver, timeout_seconds=timeout_seconds
                )
            await self.log.append(
                ApprovalResolvedEvent(
                    request.request_id,
                    decision.decision,
                    remember=decision.remember,
                    source=decision.source,
                    reason=decision.reason,
                )
            )
            return decision
        except BaseException as exc:
            record = self.log.approval(request.request_id)
            if record is not None and record.pending:
                await self.log.append(
                    ApprovalResolvedEvent(
                        request.request_id,
                        status=ApprovalStatus.UNAVAILABLE
                        if isinstance(exc, ApprovalUnavailable)
                        else ApprovalStatus.INTERRUPTED,
                        reason=str(exc) if isinstance(exc, ApprovalUnavailable) else None,
                    )
                )
            raise

    async def revoke(self, request_id: str, *, reason: str = "revoked") -> None:
        self.control.check_open()
        record = self.log.approval(request_id)
        if record is None:
            raise KeyError(request_id)
        if not record.revoked:
            await self.log.append(ApprovalRevokedEvent(request_id, reason))

    async def _answer(self, request_id: str, decision: ApprovalResponse) -> None:
        self.control.check_open()
        pending = self._live.get(request_id)
        if pending is not None:
            await pending.answer(decision)
            return
        record = self.log.approval(request_id)
        if (
            record is not None
            and record.resolution is not None
            and record.resolution.status == ApprovalStatus.ANSWERED
        ):
            if (
                record.decision is not decision.decision
                or record.resolution.remember != decision.remember
            ):
                raise ValueError("An approval cannot change its submitted decision")
            return
        latest = self.log.last(ApprovalResolvedEvent)
        if (
            latest is not None
            and latest.request_id == request_id
            and latest.status == ApprovalStatus.ANSWERED
        ):
            if latest.decision is not decision.decision or latest.remember != decision.remember:
                raise ValueError("An approval cannot change its submitted decision")
            return
        if record is not None and (
            record.pending
            or (record.resolution is not None and record.resolution.reason == _NO_RESPONDER)
        ):
            raise ApprovalUnavailable(
                "No live responder held this approval; answer it from the stream that owns "
                "its turn or open session.approvals.responder() before it is requested"
            )
        raise ApprovalUnavailable("This approval is no longer pending")

    async def _answer_request(
        self,
        request: ApprovalRequestedEvent,
        approver: Approver | None,
        *,
        timeout_seconds: float,
    ) -> ApprovalResponse:
        with span(
            "harnessed.approval_wait",
            scope=self.scope,
            session_id=self.log.id,
            tool=request.call.name,
            call_id=request.call.id,
        ) as current:
            decision = await self._wait(request, approver, timeout_seconds=timeout_seconds)
            current.set_attribute("decision", decision.decision)
            return decision

    async def _wait(
        self,
        request: ApprovalRequestedEvent,
        approver: Approver | None,
        *,
        timeout_seconds: float,
    ) -> ApprovalResponse:
        try:
            with suspend_tool_execution():
                async with asyncio.timeout(timeout_seconds):
                    decision = await self._respond(request, approver)
            if decision is None:
                raise ApprovalUnavailable(_NO_RESPONDER)
            if isinstance(decision, Decision):
                return ApprovalResponse(decision)
            if not isinstance(decision, ApprovalResponse):
                raise TypeError("Approvers must return an allow or deny response")
            return decision
        except ApprovalUnavailable:
            raise
        except TimeoutError as exc:
            raise ApprovalUnavailable(
                f"Approval response timed out after {timeout_seconds:g} seconds"
            ) from exc
        except Exception as exc:
            logger.exception("Approval responder failed for %s", request.call.name)
            raise ApprovalUnavailable(f"Approval responder failed: {type(exc).__name__}") from exc

    async def _respond(
        self, request: ApprovalRequestedEvent, approver: Approver | None
    ) -> ApprovalResponse | Decision | None:
        if PARENT_APPROVER in self.scope:
            return await self.scope[PARENT_APPROVER].ask(request)
        reviewer = self.scope.get(ApprovalReviewer)
        if reviewer is not None:
            review = ApprovalReviewRequest(request, self.log.history_entries())
            answer = await reviewer.review(review)
            if answer is not None:
                if not isinstance(answer, ApprovalResponse):
                    raise TypeError("Reviewers must return an ApprovalResponse or None")
                return replace(answer, source=ApprovalSource.REVIEWER)
        selected = approver if approver is not None else self.scope.get(Approver)
        if selected is not None:
            return await selected.ask(request)
        return await self.scope.dispatch(RequestApproval(request, self.scope[SessionContext]))
