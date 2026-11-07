import asyncio

import pytest

from harnessed import Agent, Plugin, PolicyDecision, ToolCall, ToolContext, events, tool
from harnessed.core.approvals import ApprovalState
from harnessed.core.events.approvals import ApprovalRequestedEvent, ApprovalStatus
from harnessed.core.messages import ApprovalAnswer, ToolStatus
from harnessed.core.model import ModelChunk
from harnessed.kernel import Inject
from harnessed.plugins import AgentLoop, Approvals
from harnessed.testing import ScriptedModel
from tests._audit import Audit


class Cleanup(Plugin, provides=True):
    def __init__(self):
        super().__init__()
        self.deleted: list[str] = []

    @tool
    async def cleanup(self, *, call: Inject[ToolContext]) -> str:
        if not await call.approve("Delete 3 generated files?"):
            return "kept the files"
        self.deleted.append(call.call.id)
        return "deleted"


def _calls(count: int) -> list:
    steps = [[ModelChunk(tool_call=ToolCall("cleanup", {}, f"c{i}"))] for i in range(count)]
    return [*steps, "done"]


async def test_a_tool_asks_the_configured_approver_mid_call():
    tools = Cleanup()
    answers = iter([ApprovalAnswer.DENY, ApprovalAnswer.ALWAYS])
    approvals = Approvals(default=PolicyDecision.ALLOW, approver=lambda request: next(answers))
    model = ScriptedModel(_calls(3))
    async with Agent(model=model, plugins=[tools, approvals]).session() as session:
        session_audit = Audit(session)
        result = await session.run("clean up")
        assert [r.text for _, r in result.tool_calls] == ["kept the files", "deleted", "deleted"]
        requests = [e for e in session_audit.events if isinstance(e, ApprovalRequestedEvent)]
        assert [e.scope for e in requests] == ["tool-call"] * 3
        assert requests[-1].grant_id is not None  # the ALWAYS grant answered the third call
        assert tools.deleted == []
        active = session.scope[Cleanup]
        assert active.deleted == ["c1", "c2"]


async def test_a_stream_consumer_answers_a_tool_approval():
    tools = Cleanup()
    model = ScriptedModel(_calls(1))
    plugins = [tools, Approvals(default=PolicyDecision.ALLOW)]
    async with Agent(model=model, plugins=plugins).session() as session:
        async with session.stream("clean up") as run_stream:
            async for event in run_stream:
                if isinstance(event, events.ApprovalNeededEvent):
                    assert event.reason == "Delete 3 generated files?"
                    await session.approvals.answer(event.request_id, ApprovalAnswer.ALLOW)
        assert tools.deleted == []
        active = session.scope[Cleanup]
        assert active.deleted == ["c0"]


@pytest.mark.parametrize("transport", ["callback", "stream"])
async def test_approval_waits_do_not_consume_tool_execution_time(transport):
    effects = []

    class Work(Plugin):
        @tool
        async def work(self, *, context: Inject[ToolContext]) -> str:
            if await context.approve("first") and await context.approve("second"):
                effects.append("work")
            return "done"

    async def respond(request):
        await asyncio.sleep(0.08)
        return ApprovalAnswer.ALLOW

    async with Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "done"]),
        plugins=[
            Work(),
            AgentLoop(tool_timeout=0.04),
            Approvals(
                default=PolicyDecision.ALLOW,
                approver=respond if transport == "callback" else None,
                timeout=1,
            ),
        ],
    ).session() as session:
        audit = Audit(session)
        async with asyncio.timeout(3):
            if transport == "callback":
                result = await session.run("work")
            else:
                async with session.stream("work") as stream:
                    async for event in stream:
                        if isinstance(event, events.ApprovalNeededEvent):
                            await asyncio.sleep(0.08)
                            await session.approvals.answer(event.request_id, ApprovalAnswer.ALLOW)
                    result = await stream.result()
        assert effects == ["work"]
        assert result.tool_calls[0][1].status is ToolStatus.OK
        assert len(ApprovalState(audit.events).records) == 2


async def test_approval_resumes_the_remaining_execution_budget_without_resetting_it():
    effects = []

    class Work(Plugin):
        @tool(timeout_seconds=0.1)
        async def work(self, *, context: Inject[ToolContext]) -> str:
            await asyncio.sleep(0.07)
            await context.approve("permission")
            await asyncio.sleep(0.07)
            effects.append("work")
            return "done"

    async def respond(request):
        await asyncio.sleep(0.15)
        return ApprovalAnswer.ALLOW

    async with Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "done"]),
        plugins=[Work(), Approvals(default=PolicyDecision.ALLOW, approver=respond)],
    ).session() as session:
        audit = Audit(session)
        async with asyncio.timeout(3):
            result = await session.run("work")
        assert effects == []
        assert result.tool_calls[0][1].status is ToolStatus.TIMEOUT
        assert ApprovalState(audit.events).records[-1].decision is ApprovalAnswer.ALLOW


async def test_run_cancellation_still_interrupts_a_suspended_tool_budget():
    asked = asyncio.Event()
    stopped = asyncio.Event()

    class Work(Plugin):
        @tool
        async def work(self, *, context: Inject[ToolContext]) -> str:
            await context.approve("permission")
            pytest.fail("Cancelled approval allowed execution")

    async def respond(request):
        asked.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async with Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))]]),
        plugins=[Work(), Approvals(default=PolicyDecision.ALLOW, approver=respond)],
    ).session() as session:
        audit = Audit(session)
        async with asyncio.timeout(3):
            run = await session.submit("work")
            await asked.wait()
            await run.cancel()
            result = await run.result()
        assert not result.completed and stopped.is_set()
        assert not session.approvals.pending
        assert (
            ApprovalState(audit.events).records[-1].resolution.status is ApprovalStatus.INTERRUPTED
        )


async def test_a_parallel_approval_wait_does_not_suspend_other_tools():
    class Work(Plugin):
        @tool(parallel=True)
        async def approved(self, *, context: Inject[ToolContext]) -> str:
            await context.approve("permission")
            return "approved"

        @tool(parallel=True)
        async def slow(self) -> str:
            await asyncio.sleep(1)
            pytest.fail("Parallel tool escaped its execution timeout")

    async def respond(request):
        await asyncio.sleep(0.08)
        return ApprovalAnswer.ALLOW

    async with Agent(
        model=ScriptedModel(
            [
                [
                    ModelChunk(tool_call=ToolCall("approved")),
                    ModelChunk(tool_call=ToolCall("slow")),
                ],
                "done",
            ]
        ),
        plugins=[
            Work(),
            AgentLoop(tool_timeout=0.04),
            Approvals(default=PolicyDecision.ALLOW, approver=respond),
        ],
    ).session() as session:
        async with asyncio.timeout(3):
            result = await session.run("work")
        assert [receipt.status for _, receipt in result.tool_calls] == [
            ToolStatus.OK,
            ToolStatus.TIMEOUT,
        ]


async def test_nested_approval_waits_preserve_the_suspended_execution_budget():
    class Work(Plugin):
        @tool(timeout_seconds=0.04)
        async def work(self, *, context: Inject[ToolContext]) -> str:
            assert await context.approve("outer")
            return "approved"

    async def respond(request):
        if request.reason == "outer":
            await session.approvals.request(request.call, "inner", scope="nested")
        await asyncio.sleep(0.08)
        return ApprovalAnswer.ALLOW

    async with Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "done"]),
        plugins=[Work(), Approvals(default=PolicyDecision.ALLOW, approver=respond)],
    ).session() as session:
        audit = Audit(session)
        async with asyncio.timeout(3):
            result = await session.run("work")
        assert result.tool_calls[0][1].status is ToolStatus.OK
        assert [record.decision for record in ApprovalState(audit.events).records] == [
            ApprovalAnswer.ALLOW,
            ApprovalAnswer.ALLOW,
        ]


async def test_background_approval_cannot_suspend_its_parent_tasks_execution():
    stopped = asyncio.Event()

    class Work(Plugin):
        @tool(timeout_seconds=0.04)
        async def work(self, *, context: Inject[ToolContext]) -> str:
            question = asyncio.create_task(context.approve("permission"))
            try:
                await asyncio.sleep(1)
            finally:
                question.cancel()
                await asyncio.gather(question, return_exceptions=True)
            pytest.fail("Background approval suspended the parent tool")

    async def respond(request):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async with Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "done"]),
        plugins=[Work(), Approvals(default=PolicyDecision.ALLOW, approver=respond)],
    ).session() as session:
        async with asyncio.timeout(3):
            result = await session.run("work")
        assert result.tool_calls[0][1].status is ToolStatus.TIMEOUT
        assert stopped.is_set() and not session.approvals.pending
