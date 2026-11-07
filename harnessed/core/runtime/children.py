from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from hashlib import sha256
from typing import TYPE_CHECKING

from harnessed.core.children import ChildLimits, ChildSessionFactory
from harnessed.core.events.subagents import (
    SubagentFinishedEvent,
    SubagentStartedEvent,
    SubagentStatus,
)
from harnessed.core.hooks import SessionAborting, SessionClosing
from harnessed.core.messages import ToolResult, ToolStatus, UserMessage, to_dict
from harnessed.core.session.children import ChildSession
from harnessed.core.tools import ToolContext, ToolError

from .child_execution import ChildExecution

if TYPE_CHECKING:
    from .session import Session


class ChildSessionSupervisor:
    """Own child lifetimes independently of individual coordinator tool waiters."""

    def __init__(
        self,
        parent: Session,
        agents: Mapping[str, ChildSessionFactory],
        limits: ChildLimits,
    ) -> None:
        self.parent = parent
        self.agents = agents
        self.limits = limits
        self.slots = asyncio.Semaphore(limits.max_parallel)
        self.closed = False
        self.calls = {child.call_id for child in parent.children}
        self._executions: dict[str, ChildExecution] = {}
        self._draining = False

    async def delegate(self, agent: str, task: str, context: ToolContext) -> ToolResult:
        child = await self._start(agent, task, context, background=False)
        execution = self._executions.get(child.id)
        if child.status is not SubagentStatus.RUNNING or execution is None:
            return _receipt(child)
        try:
            receipt = await asyncio.shield(execution.completion)
        except asyncio.CancelledError:
            await execution.cancel(notify=False)
            raise
        if isinstance(receipt, Exception):
            raise receipt
        return _result(receipt)

    async def spawn(self, agent: str, task: str, context: ToolContext) -> ToolResult:
        child = await self._start(agent, task, context, background=True)
        return _view(child)

    async def status(self, child_id: str, timeout: float) -> ToolResult:
        _validate_timeout(timeout)
        child = self._child(child_id)
        execution = self._executions.get(child_id)
        if child.status is SubagentStatus.RUNNING and execution is not None:
            await execution.status(timeout)
        return _view(self._child(child_id))

    async def send(self, child_id: str, message: str) -> ToolResult:
        child = self._child(child_id)
        if not message.strip():
            raise ToolError("A child message must not be empty")
        execution = self._executions.get(child_id)
        if child.status is not SubagentStatus.RUNNING or execution is None:
            raise ToolError("Child task has finished; spawn a new task instead")
        await execution.send(UserMessage(message))
        return ToolResult(json.dumps({"child_id": child_id, "accepted": True}))

    async def wait(self, child_id: str, timeout: float) -> ToolResult:
        _validate_timeout(timeout)
        child = self._child(child_id)
        execution = self._executions.get(child_id)
        if child.status is SubagentStatus.RUNNING and execution is not None:
            await asyncio.wait((execution.completion,), timeout=timeout)
            if execution.completion.done():
                result = execution.completion.result()
                if isinstance(result, Exception):
                    raise result
        child = self._child(child_id)
        return _view(child)

    async def cancel(self, child_id: str) -> ToolResult:
        child = self._child(child_id)
        execution = self._executions.get(child_id)
        if child.status is SubagentStatus.RUNNING and execution is not None:
            await execution.cancel()
        return _view(self._child(child_id))

    async def aborting(self, event: SessionAborting) -> None:
        if event.log is self.parent.log:
            await self._drain()

    async def closing(self, event: SessionClosing) -> None:
        if event.session.log is self.parent.log:
            await self.aclose()

    async def aclose(self) -> None:
        self.closed = True
        await self._drain()

    async def _start(
        self, agent: str, task: str, context: ToolContext, *, background: bool
    ) -> ChildSession:
        if self.closed or self._draining:
            raise ToolError("Subagents is closed or aborting")
        if agent not in self.agents:
            raise ToolError(f"Unknown subagent {agent!r}; choose from {', '.join(self.agents)}")
        if not task.strip():
            raise ToolError("A subagent task must not be empty")
        parent = self.parent.parent
        depth = parent.depth + 1 if parent is not None else 1
        depth_limit = (
            min(self.limits.max_depth, parent.depth_limit)
            if parent is not None
            else self.limits.max_depth
        )
        if depth > depth_limit:
            raise ToolError("Subagent depth limit reached")
        existing = next(
            (child for child in self.parent.children if child.call_id == context.call.id), None
        )
        if existing is not None:
            if (
                existing.agent != agent
                or existing.task != task
                or existing.background != background
            ):
                raise ToolError(
                    "Delegation call ID was already used with a different agent or task"
                )
            if not background and existing.status is SubagentStatus.RUNNING:
                raise ToolError("This delegation is already running; inspect its child session")
            return existing
        if context.call.id in self.calls:
            raise ToolError("This delegation is already starting")
        if len(self.calls) >= self.limits.max_calls:
            raise ToolError("Subagent call limit reached for this session")
        turn = self.parent.log.open_turn
        if turn is None:
            raise ToolError("Delegation requires an active parent turn")
        digest = sha256(f"{self.parent.id}:{context.call.id}".encode()).hexdigest()
        started = SubagentStartedEvent(
            f"child-{digest[:48]}",
            agent,
            task,
            turn.id,
            context.call.id,
            depth,
            background=background,
        )
        self.calls.add(context.call.id)
        execution = ChildExecution(
            self.parent,
            self.agents[agent],
            started,
            self.limits,
            depth_limit,
            self.slots,
        )
        self._executions[started.child_session_id] = execution
        execution.task = asyncio.create_task(
            execution.run(),
            name=f"harnessed:child:{started.child_session_id}",
        )
        try:
            error = await asyncio.shield(execution.admitted)
            if error is not None:
                raise error
        except asyncio.CancelledError:
            await execution.cancel(notify=False)
            raise
        return self._child(started.child_session_id)

    async def _drain(self) -> None:
        self._draining = True
        try:
            pending = tuple(
                execution
                for execution in self._executions.values()
                if execution.task is not None and not execution.task.done()
            )
            if not pending:
                return
            drained = asyncio.gather(*(item.cancel(notify=False) for item in pending))
            cancelled = False
            while not drained.done():
                try:
                    await asyncio.shield(drained)
                except asyncio.CancelledError:
                    cancelled = True
            drained.result()
            if cancelled:
                raise asyncio.CancelledError
        finally:
            self._draining = False

    def _child(self, child_id: str) -> ChildSession:
        child = next((item for item in self.parent.children if item.id == child_id), None)
        if child is None:
            raise ToolError(f"Unknown child session {child_id!r} for this parent")
        return child


def _validate_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or not 0 <= timeout <= 3600:
        raise ToolError("Timeout must be between 0 and 3600 seconds")


def _view(child: ChildSession) -> ToolResult:
    return ToolResult(
        json.dumps(
            {
                "child_id": child.id,
                "agent": child.agent,
                "status": child.status,
                **to_dict(child.runtime),
                "text": child.text,
                "usage": to_dict(child.usage),
            }
        )
    )


def _receipt(child: ChildSession) -> ToolResult:
    return _result(SubagentFinishedEvent(child.id, child.status, child.text, child.usage))


def _result(receipt: SubagentFinishedEvent) -> ToolResult:
    status = (
        ToolStatus.OK
        if receipt.status is SubagentStatus.COMPLETED
        else ToolStatus.TIMEOUT
        if receipt.status is SubagentStatus.TIMED_OUT
        else ToolStatus.CANCELLED
        if receipt.status is SubagentStatus.CANCELLED
        else ToolStatus.ERROR
    )
    return ToolResult(json.dumps(to_dict(receipt)), status)
