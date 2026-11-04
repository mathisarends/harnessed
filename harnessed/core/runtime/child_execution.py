from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from time import time
from typing import TYPE_CHECKING
from uuid import uuid4

from harnessed.core.child_status import (
    ChildActivity,
    ChildReport,
    ChildReportState,
    ChildRuntimeState,
)
from harnessed.core.children import ChildLimits, ChildSessionFactory, ChildSessionRequest
from harnessed.core.errors import SessionStateError
from harnessed.core.hooks import SessionAppended
from harnessed.core.messages import ApprovalAnswer, ToolCall, UserMessage, to_dict
from harnessed.core.model import ModelUsage
from harnessed.core.session.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ModelRequestedEvent,
    StepEndedEvent,
    SubagentApprovalRequestedEvent,
    SubagentFinishedEvent,
    SubagentLinkedEvent,
    SubagentMessageSentEvent,
    SubagentStartedEvent,
    SubagentStatus,
    SubagentUpdatedEvent,
    ToolCallFinishedEvent,
    ToolCallRequestedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolProgressEvent,
    TurnEndedEvent,
)
from harnessed.core.tools import ToolError
from harnessed.core.tools.output_limit import ToolOutputLimit

if TYPE_CHECKING:
    from .session import Session

logger = logging.getLogger(__name__)


class ParentApprover:
    def __init__(self, parent: Session, child_id: str, agent: str) -> None:
        self.parent = parent
        self.child_id = child_id
        self.agent = agent

    async def ask(self, request: ApprovalRequestedEvent) -> ApprovalAnswer:
        call = ToolCall(
            request.call.name,
            request.call.arguments,
            id=f"{self.child_id}-{request.request_id}",
        )
        await self.parent.log.append(
            SubagentApprovalRequestedEvent(self.child_id, request.request_id, call.id)
        )
        decision = await self.parent.approvals.request(
            call,
            f"Subagent {self.agent} ({self.child_id}): {request.reason}",
            scope=f"subagent:{self.child_id}:{request.scope}",
        )
        # Grants remain parent-owned so revocation takes effect on the next request.
        return ApprovalAnswer.ALLOW if decision is ApprovalAnswer.ALWAYS else decision


@dataclass
class StatusRequest:
    id: str
    completion: asyncio.Future[ChildReport]
    notify_late: bool = False


class ChildExecution:
    """One session-owned driver, with correlated reports and bounded observation."""

    def __init__(
        self,
        parent: Session,
        factory: ChildSessionFactory,
        started: SubagentStartedEvent,
        limits: ChildLimits,
        depth_limit: int,
        slots: asyncio.Semaphore,
    ) -> None:
        self.parent = parent
        self.factory = factory
        self.started = started
        self.limits = limits
        self.depth_limit = depth_limit
        self.slots = slots
        loop = asyncio.get_running_loop()
        self.admitted: asyncio.Future[Exception | None] = loop.create_future()
        self.completion: asyncio.Future[SubagentFinishedEvent | Exception] = loop.create_future()
        self.task: asyncio.Task[None] | None = None
        self.child: Session | None = None
        self.runtime = ChildRuntimeState(step_limit=limits.max_steps)
        self.request: StatusRequest | None = None
        self._messages: list[UserMessage] = []
        self._tools: dict[str, str] = {}
        self._calls: dict[str, str] = {}
        self._approvals: set[str] = set()
        self._report_lock = asyncio.Lock()
        self._running = False
        self._ready = False
        self._run_id: str | None = None
        self._accepting = True
        self._cancelled = False
        self._notify = started.background

    async def run(self) -> None:
        self._running = True
        log = self.parent.log
        status = SubagentStatus.FAILED
        text = "Child session failed to start"
        child: Session | None = None
        try:
            await log.append(self.started)
            await log.flush()
            await self._update(self.runtime)
            self.admitted.set_result(None)
            if self._cancelled:
                raise asyncio.CancelledError
            async with asyncio.timeout(self.limits.timeout), self.slots:
                request = ChildSessionRequest(
                    self.started.child_session_id,
                    SubagentLinkedEvent(
                        self.parent.id,
                        self.started.turn_id,
                        self.started.call_id,
                        self.started.agent,
                        self.started.depth,
                        self.depth_limit,
                    ),
                    ParentApprover(self.parent, self.started.child_session_id, self.started.agent),
                    self,
                )
                async with self.factory.child_session(request) as child:
                    self.child = child
                    remove = child.ctx.on(SessionAppended, self._observe)
                    try:
                        await self._update(replace(self.runtime, activity=ChildActivity.IDLE))
                        handle = await child.submit(
                            self.started.task,
                            max_steps=self.limits.max_steps,
                            token_budget=self.limits.token_budget,
                        )
                        self._run_id = handle.id
                        self._ready = True
                        if self._accepting:
                            for message in self._messages:
                                try:
                                    await self._deliver(message)
                                except ToolError:
                                    break
                        self._messages.clear()
                        result = await handle.result()
                        status = (
                            SubagentStatus.COMPLETED if result.completed else SubagentStatus.LIMITED
                        )
                        text = result.text
                    finally:
                        self._accepting = False
                        await remove()
                        # Cancelling a result waiter alone does not stop the child driver.
                        await child.abort()
        except asyncio.CancelledError:
            status = SubagentStatus.CANCELLED
            text = "Delegation cancelled"
        except TimeoutError:
            status = SubagentStatus.TIMED_OUT
            text = "Delegation timed out"
        except Exception as exc:
            logger.exception("Subagent %s failed", self.started.agent)
            status = SubagentStatus.FAILED
            text = f"Subagent failed: {type(exc).__name__}"
        finally:
            self._accepting = False
            # Receipt persistence is owned work too; cancellation must not strand its waiters.
            finishing = asyncio.create_task(self._finish(status, text, child))
            while not finishing.done():
                try:
                    await asyncio.shield(finishing)
                except asyncio.CancelledError:
                    pass
            finishing.result()

    async def cancel(self, *, notify: bool = True) -> None:
        self._notify = self._notify and notify
        self._cancelled = True
        task = self.task
        if task is None:
            return
        if self._running and not task.done() and not task.cancelling():
            task.cancel()
        # A queued task must enter run() before it can record its cancellation.
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def send(self, message: UserMessage) -> None:
        if not self._accepting:
            raise ToolError("Child task has finished; spawn a new task instead")
        await self.parent.log.append(
            SubagentMessageSentEvent(self.started.child_session_id, message)
        )
        await self.parent.log.flush()
        await self._deliver(message)

    async def status(self, timeout: float) -> None:
        async with self._report_lock:
            if self.completion.done() or not self._accepting:
                return
            request = self.request
            if request is None or request.completion.done():
                request = StatusRequest(uuid4().hex, asyncio.get_running_loop().create_future())
                self.request = request
                await self._update(
                    replace(
                        self.runtime,
                        report=ChildReport(request.id, ChildReportState.PENDING),
                    )
                )
                await self._deliver(
                    UserMessage(
                        json.dumps(
                            {
                                "type": "subagent.status.request",
                                "request_id": request.id,
                                "instruction": "Call answer_status(request_id, message) with "
                                "completed work, current work and blockers. "
                                "Then continue the original task. "
                                "This request is not a new task and must not end your task.",
                            }
                        )
                    )
                )
        try:
            await asyncio.wait((request.completion,), timeout=timeout)
        finally:
            if not request.completion.done():
                request.notify_late = True

    async def answer(self, request_id: str, text: str) -> None:
        request = self.request
        if request is None or request.id != request_id or request.completion.done():
            raise ToolError("No pending status request with that ID")
        if not text.strip():
            raise ToolError("Status report must not be empty")
        # Serialize replies so observers cannot admit a duplicate response while persisting.
        report = ChildReport(request_id, ChildReportState.ANSWERED, text)
        async with self._report_lock:
            if request.completion.done():
                raise ToolError("This status request has already been answered")
            await self._update(replace(self.runtime, report=report))
            await self.parent.log.flush()
            request.completion.set_result(report)
        if request.notify_late:
            await self._notify_parent("subagent.status", to_dict(report))

    async def _finish(
        self,
        status: SubagentStatus,
        text: str,
        child: Session | None,
    ) -> None:
        try:
            visible = any(item.id == self.started.child_session_id for item in self.parent.children)
            if not self.admitted.done():
                self.admitted.set_result(RuntimeError("Child admission interrupted"))
            if visible:
                request = self.request
                if request is not None and not request.completion.done():
                    report = ChildReport(request.id, ChildReportState.UNAVAILABLE)
                    await self._update(replace(self.runtime, report=report))
                    request.completion.set_result(report)
                receipt = SubagentFinishedEvent(
                    self.started.child_session_id,
                    status,
                    text,
                    child.tree_usage if child is not None else ModelUsage(0),
                )
                await self.parent.log.append(receipt)
                await self.parent.log.flush()
                self.completion.set_result(receipt)
                if self._notify:
                    await self._notify_parent("subagent.finished", to_dict(receipt))
            else:
                self.completion.set_result(RuntimeError("Child admission failed before start"))
        except Exception as exc:
            if not self.completion.done():
                self.completion.set_result(exc)
            self.parent.control.fail(exc)
        finally:
            self.child = None
            self._messages.clear()
            self._tools.clear()
            self._calls.clear()
            self._approvals.clear()

    async def _deliver(self, message: UserMessage) -> None:
        if self._ready and self.child is not None:
            if not self._accepting:
                raise ToolError("Child task has finished")
            try:
                await self.child.inbox.steer(message, during_run=self._run_id)
            except SessionStateError as exc:
                raise ToolError("Child task has finished; spawn a new task instead") from exc
        else:
            self._messages.append(message)

    async def _update(self, runtime: ChildRuntimeState) -> None:
        self.runtime = runtime
        await self.parent.log.append(SubagentUpdatedEvent(self.started.child_session_id, runtime))

    async def _observe(self, event: SessionAppended) -> None:
        if self.child is None or event.log is not self.child.log:
            return
        record = event.event
        state = self.runtime
        match record:
            case ModelRequestedEvent():
                state = replace(state, activity=ChildActivity.MODEL)
            case StepEndedEvent():
                state = replace(
                    state,
                    steps_completed=state.steps_completed + 1,
                    activity=ChildActivity.IDLE,
                )
            case ToolCallRequestedEvent(call=call):
                self._calls[call.id] = call.name
                state = replace(state, activity=ChildActivity.IDLE)
            case ToolExecutionStartedEvent(call_id=call_id):
                self._tools[call_id] = self._calls[call_id]
                state = replace(state, activity=ChildActivity.TOOL)
            case ToolExecutionFinishedEvent(call_id=call_id):
                self._tools.pop(call_id, None)
                state = replace(state, activity=ChildActivity.IDLE)
            case ToolCallFinishedEvent(call=call):
                self._tools.pop(call.id, None)
                self._calls.pop(call.id, None)
                state = replace(state, activity=ChildActivity.IDLE)
            case ApprovalRequestedEvent(request_id=request_id):
                self._approvals.add(request_id)
            case ApprovalResolvedEvent(request_id=request_id):
                self._approvals.discard(request_id)
                state = replace(state, activity=ChildActivity.IDLE)
            case ToolProgressEvent(text=text):
                state = replace(state, last_progress=text[:4096], progress_at=time())
                await self.parent.log.append(
                    ToolProgressEvent(
                        self.started.call_id,
                        f"[{self.started.agent} {self.started.child_session_id}] {text}",
                    )
                )
            case TurnEndedEvent():
                self._accepting = False
            case _:
                return
        if self._approvals:
            state = replace(state, activity=ChildActivity.WAITING_APPROVAL)
        elif self._tools:
            state = replace(state, activity=ChildActivity.TOOL)
        state = replace(state, active_tools=tuple(self._tools.values()))
        await self._update(state)

    async def _notify_parent(self, kind: str, payload: dict[str, object]) -> None:
        control = self.parent.control
        if (
            self._cancelled
            or control.closing
            or control.error is not None
            or self.parent.log.control.stopped
        ):
            return
        # Notifications carry a preview; fetching the full result remains a reviewed tool call.
        payload = dict(payload)
        text = payload.get("text")
        preview_limit = min(4096, self.parent.ctx[ToolOutputLimit].max_chars // 2)
        if isinstance(text, str) and len(text) > preview_limit:
            payload["text"] = text[:preview_limit]
            payload["truncated"] = True
        await self.parent.steer(
            json.dumps(
                {
                    "type": kind,
                    "child_id": self.started.child_session_id,
                    "data": payload,
                }
            )
        )
