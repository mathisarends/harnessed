"""Run with: uv run python -m examples.applications.01_approvals"""

import asyncio
from enum import StrEnum
from time import perf_counter

from harnessed import Agent, Approvals, Decision, Plugin, tool
from harnessed.errors import ApprovalUnavailable
from harnessed.events import ApprovalStatus, ToolCallFinishedEvent
from harnessed.hooks import RequestApproval, ToolPolicy
from harnessed.kernel import hook
from harnessed.models import ModelChunk, ToolCall, ToolStatus
from harnessed.models.messages import ApprovalResponse
from harnessed.testing import ScriptedModel, call


class DocumentAction(StrEnum):
    PUBLISH = "publish"


async def without_ui():
    class HeadlessTools(Plugin):
        @tool(action=DocumentAction.PUBLISH)
        async def publish(self) -> str:
            raise AssertionError("Publishing requires approval")

    tools = HeadlessTools()

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("publish"))], "Approval unavailable."])
    async with Agent(
        model=model,
        approvals=Approvals(ask=[DocumentAction.PUBLISH]),
        plugins=[tools],
    ).session() as session:
        result = await session.run("Publish the artifact.")
        returned = next(
            event for event in result.events if isinstance(event, ToolCallFinishedEvent)
        )
        assert returned.result.status == ToolStatus.UNAVAILABLE
        assert not session.approvals.pending
        print(f"No UI: {returned.result.status}; action was not executed.")


async def remembered_approval():
    asked = []

    class Publisher(Plugin):
        @tool(action=DocumentAction.PUBLISH)
        async def publish(self) -> str:
            return "Published."

    async def approve(request):
        asked.append(request.call.name)
        # remember=True allows matching calls for the rest of this session.
        return ApprovalResponse(Decision.ALLOW, remember=True)

    model = ScriptedModel([call("publish"), call("publish"), "Published twice."])
    agent = Agent(
        model=model,
        approvals=Approvals(ask=[DocumentAction.PUBLISH], approver=approve),
        plugins=[Publisher()],
    )
    result = await agent.run("Publish the artifact twice.")
    assert asked == ["publish"]
    print(f"Remembered approval: asked {len(asked)} time for 2 calls; {result.text}")


async def listener_and_priorities():
    order = []

    class ApprovedTools(Plugin):
        @tool(action=DocumentAction.PUBLISH)
        async def publish(self) -> str:
            order.append("execute")
            return "Artifact published by the offline example."

        @hook(ToolPolicy, priority=0)
        async def timing(self, _event, next_):
            order.append("timer")
            start = perf_counter()
            result = await next_()
            print(f"Downstream policy time: {(perf_counter() - start) * 1000:.1f} ms")
            return result

        @hook(ToolPolicy, priority=100)
        async def check(self, _event, next_):
            order.append("policy")
            await asyncio.sleep(0.005)
            return await next_()

        @hook(RequestApproval)
        async def answer(self, _event):
            order.append("approval")
            start = perf_counter()
            await asyncio.sleep(0.025)
            print(f"Approval response time: {(perf_counter() - start) * 1000:.1f} ms")
            return Decision.ALLOW

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("publish"))], "Published."])
    agent = Agent(
        model=model,
        approvals=Approvals(ask=[DocumentAction.PUBLISH]),
        plugins=[ApprovedTools()],
    )

    async with agent.session() as session:
        await session.run("Publish the artifact.")
        assert order == ["timer", "policy", "approval", "execute"]
        print("Order:", " -> ".join(order))


async def response_timeout():
    stopped = asyncio.Event()

    async def disconnected(_request):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async with Agent(
        model=ScriptedModel([]), approvals=Approvals(timeout=0.02, approver=disconnected)
    ).session() as session:
        try:
            await session.approvals.request(ToolCall("publish"), "Publish the artifact?")
        except ApprovalUnavailable as error:
            print(f"SDK: {error}")
        assert stopped.is_set() and (not session.approvals.pending)
        resolution = session.approvals.records[-1].resolution
        assert resolution is not None and resolution.status is ApprovalStatus.UNAVAILABLE


async def main():
    await without_ui()
    await remembered_approval()
    await listener_and_priorities()
    await response_timeout()


if __name__ == "__main__":
    asyncio.run(main())
