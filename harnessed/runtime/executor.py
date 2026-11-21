from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace

from harnessed.approvals import Approvals
from harnessed.approvals.session import SessionApprovals
from harnessed.context.tool_output_limit import ToolOutputLimit
from harnessed.errors import ApprovalUnavailable
from harnessed.events import (
    PolicySource,
    ToolCallFinishedEvent,
    ToolCallRequestedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    ToolPolicyAppliedEvent,
)
from harnessed.kernel import Scope
from harnessed.models import ApprovalSource, Decision, ToolCall, ToolResult, ToolStatus
from harnessed.models.messages import DenialSource
from harnessed.sessions import SessionContext
from harnessed.sessions.log import SessionLog
from harnessed.tools import (
    OutputLimit,
    PreparedToolCall,
    ToolChain,
    ToolContexts,
    ToolRegistry,
    ToolScheduler,
    ToolScheduling,
    format_tool_error,
)
from harnessed.tracing import span

from .hooks import (
    ExecuteTool,
    ReviewToolResult,
    ToolPolicy,
)
from .options import AgentOptions


@dataclass
class ToolBatch:
    """A monotonic boundary shared by workers of one assistant response."""

    broken: bool = False


class ToolExecutor:
    def __init__(
        self,
        scope: Scope,
        log: SessionLog,
        options: AgentOptions,
        approvals: SessionApprovals,
        context: SessionContext,
    ) -> None:
        self.scope = scope
        self.log = log
        self.options = options
        self.approvals = approvals
        self.context = context

    async def run(
        self,
        calls: tuple[ToolCall, ...],
        *,
        results: Mapping[str, ToolResult] | None = None,
        allow_execution: bool = True,
    ) -> None:
        if not calls:
            return
        plans: list[PreparedToolCall] = []
        registry = self.scope[ToolRegistry]
        for call in calls:
            await self.log.append(ToolCallRequestedEvent(call))
            if not allow_execution:
                await self.skip(call, "Application actions are disabled in the final step")
                continue
            if results is not None and call.id in results:
                plans.append(PreparedToolCall(call, ToolScheduling(), results[call.id]))
                continue
            try:
                plans.append(registry.plan(call))
            except Exception as exc:
                # Preserve the batch when arbitrary tool-planning code fails.
                plans.append(
                    PreparedToolCall(
                        call,
                        ToolScheduling(),
                        ToolResult.error(
                            format_tool_error(call, exc, self.options.error_formatter)
                        ),
                    )
                )
        scheduler = ToolScheduler(self.options.max_parallel_tools)
        batch = ToolBatch()
        for group in scheduler.groups(plans):
            await self._run_group(group, scheduler, batch)

    async def skip(self, call: ToolCall, reason: str) -> None:
        await self.log.append(ToolCallFinishedEvent(call, ToolResult(reason, ToolStatus.SKIPPED)))

    async def invoke(self, plan: PreparedToolCall, batch: ToolBatch) -> tuple[ToolResult, bool]:
        with span(
            "harnessed.tool",
            scope=self.scope,
            session_id=self.log.id,
            tool=plan.call.name,
            call_id=plan.call.id,
        ) as current:
            current.set_attribute("input", plan.call)
            result, reviewable = await self.execute(plan, batch)
            current.set_attribute("output", result)
            return result, reviewable

    async def execute(self, plan: PreparedToolCall, batch: ToolBatch) -> tuple[ToolResult, bool]:
        call = plan.call
        execution = self.scope[ToolContexts].create(call)
        execution.approval_identity = plan.grant_key
        result: ToolResult | None = None
        reviewable = False
        try:
            await self.log.append(ToolExecutionStartedEvent(call.id))
            returned = await self.scope.dispatch(
                ExecuteTool(call, self.context, tool=plan.tool, envelope=plan.envelope),
                default=lambda: self.scope[ToolRegistry].execute(
                    call,
                    scope=self.scope,
                    call_context=execution,
                    _expected=plan.binding,
                    error_formatter=self.options.error_formatter,
                    default_timeout=self.options.tool_timeout,
                ),
            )
            if not isinstance(returned, ToolResult):
                raise TypeError("Tool hooks must return a ToolResult")
            result = returned
            reviewable = True
        except asyncio.CancelledError:
            execution.signal.set()
            result = ToolResult("Tool execution cancelled", ToolStatus.CANCELLED)
            raise
        except Exception as exc:
            # User tools may raise any exception; preserve it as a durable tool error result.
            result = ToolResult.error(format_tool_error(call, exc, self.options.error_formatter))
        finally:
            if result is not None:
                if plan.tool is not None and plan.tool.chain is ToolChain.STOP_AFTER:
                    result = replace(result, break_chain=True)
                if result.break_chain:
                    batch.broken = True
            if result is not None and self.log.execution_pending(call.id):
                limit = self._output_limit(plan)
                receipt = limit.apply(result) if limit is not None else result
                await self.log.append(ToolExecutionFinishedEvent(call.id, receipt))
        assert result is not None
        return result, reviewable

    async def _run_group(
        self, plans: tuple[PreparedToolCall, ...], scheduler: ToolScheduler, batch: ToolBatch
    ) -> None:
        reviewed = [asyncio.Event() for _ in plans]

        async def worker(index: int, plan: PreparedToolCall) -> None:
            try:
                limit = self._output_limit(plan)
                async with scheduler.slots:
                    result, reviewable = await self._attempt(plan, batch)
                if index:
                    await reviewed[index - 1].wait()
                if reviewable:
                    requested_break = result.break_chain
                    result = await self._review(plan, result)
                    if requested_break and not result.break_chain:
                        result = replace(result, break_chain=True)
                    if result.break_chain:
                        batch.broken = True
                if limit is not None:
                    result = limit.apply(result)
                await self.log.append(ToolCallFinishedEvent(plan.call, result))
            finally:
                reviewed[index].set()

        await scheduler.run(plans, worker)

    def _output_limit(self, plan: PreparedToolCall) -> ToolOutputLimit | None:
        match plan.output_limit:
            case None:
                return None
            case OutputLimit():
                return self.options.tool_output_limit
            case _:
                return ToolOutputLimit(plan.output_limit)

    async def _attempt(self, plan: PreparedToolCall, batch: ToolBatch) -> tuple[ToolResult, bool]:
        """Authorize and invoke one call unless an earlier tool broke the action chain."""
        if batch.broken:
            return ToolResult(_CHAIN_BROKEN, ToolStatus.SKIPPED), False
        result = await self._authorize(plan)
        if result is not None:
            return result, False
        # Approval can yield while another worker breaks the chain.
        if batch.broken:
            return ToolResult(_CHAIN_BROKEN, ToolStatus.SKIPPED), False
        return await self.invoke(plan, batch)

    async def _authorize(self, plan: PreparedToolCall) -> ToolResult | None:
        call = plan.call
        try:
            if plan.error is not None:
                return plan.error
            unavailable = self.scope[ToolRegistry].check_enabled(plan, scope=self.scope)
            if unavailable is not None:
                return unavailable
            return await self._apply_policy(plan)
        except ApprovalUnavailable as exc:
            await self.log.append(
                ToolPolicyAppliedEvent(call.id, Decision.DENY, PolicySource.UNAVAILABLE)
            )
            return ToolResult.unavailable(str(exc))
        except Exception as exc:
            # Policy hooks are user code; report their failures through the tool result boundary.
            return ToolResult.error(format_tool_error(call, exc, self.options.error_formatter))

    async def _apply_policy(self, plan: PreparedToolCall) -> ToolResult | None:
        """Record the policy decision; a denial result, or None when the call may run."""
        call = plan.call
        previous = self.log.latest_approval(call.id)
        decision = self.scope[Approvals].decide(plan)
        if decision is not Decision.DENY:
            restriction = await self.scope.dispatch(
                ToolPolicy(call, self.context, tool=plan.tool, envelope=plan.envelope),
                default=lambda: Decision.ALLOW,
            )
            if not isinstance(restriction, Decision):
                raise TypeError("ToolPolicy hooks must return Decision")
            if restriction is not Decision.ALLOW:
                decision = restriction
        if decision is Decision.ASK:
            answer = await self.approvals.request(
                call, "Tool policy requested approval", identity=plan.grant_key
            )
            decision = Decision.DENY if answer is Decision.DENY else Decision.ALLOW
        record = self.log.latest_approval(call.id)
        answered = record is not None and record is not previous
        source = (
            PolicySource.APPROVAL
            if answered and (decision is Decision.ALLOW or record.decision is Decision.DENY)
            else PolicySource.POLICY
        )
        await self.log.append(ToolPolicyAppliedEvent(call.id, decision, source))
        if decision is not Decision.DENY:
            return None
        if answered and record.decision is Decision.DENY:
            resolution = record.resolution
            if resolution is not None and resolution.source is ApprovalSource.REVIEWER:
                return ToolResult.denied(
                    "Reviewer declined this action", source=DenialSource.REVIEWER
                )
            return ToolResult.denied("User declined this action", source=DenialSource.USER)
        return ToolResult.denied("Denied by policy", source=DenialSource.POLICY)

    async def _review(
        self,
        plan: PreparedToolCall,
        result: ToolResult,
    ) -> ToolResult:
        try:
            result = await self.scope.dispatch(
                ReviewToolResult(
                    plan.call,
                    result,
                    self.context,
                    output_limit=plan.output_limit,
                    tool=plan.tool,
                    envelope=plan.envelope,
                ),
                default=lambda: result,
            )
            if not isinstance(result, ToolResult):
                raise TypeError("Tool hooks must return a ToolResult")
            return result
        except Exception as exc:
            # Review hooks are user code; convert any failure into the reviewed call's error result.
            return ToolResult.error(format_tool_error(plan.call, exc, self.options.error_formatter))


_CHAIN_BROKEN = "Skipped because an earlier tool ended this action chain"
