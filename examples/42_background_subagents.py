import asyncio
import json

from harnessed import Agent, Plugin, SystemPrompt, ToolCall, tool
from harnessed.events import SubagentUpdatedEvent
from harnessed.models import ModelChunk, ModelRequest
from harnessed.plugins import Approvals, Subagents
from harnessed.plugins.subagents import ChildReportState
from harnessed.testing import CallbackModel


async def main() -> None:
    report_requested = asyncio.Event()
    finish_work = asyncio.Event()

    class ReviewTools(Plugin):
        @tool(readonly=True)
        async def inspect(self) -> str:
            await asyncio.wait_for(report_requested.wait(), 5)
            return "Initial inspection complete"

    child_steps = 0

    async def child_model(request: ModelRequest):
        nonlocal child_steps
        child_steps += 1
        if child_steps == 1:
            yield ModelChunk(tool_call=ToolCall("inspect"))
        elif child_steps == 2:
            inquiry = next(
                json.loads(message.text)
                for message in request.messages
                if message.text.startswith('{"type": "subagent.status.request"')
            )
            yield ModelChunk(
                tool_call=ToolCall(
                    "answer_status",
                    {
                        "request_id": inquiry["request_id"],
                        "message": "Inspection done; checking races.",
                    },
                )
            )
        else:
            await asyncio.wait_for(finish_work.wait(), 5)
            yield ModelChunk(text="Review complete: refresh requests need synchronization.")

    parent_steps = 0
    child_id = ""

    async def coordinator_model(request: ModelRequest):
        nonlocal parent_steps, child_id
        parent_steps += 1
        if parent_steps == 1:
            yield ModelChunk(
                tool_call=ToolCall(
                    "spawn",
                    {"agent": "review", "task": "Review refresh request synchronization."},
                )
            )
        elif parent_steps == 2:
            child_id = json.loads(request.messages[-1].text)["child_id"]
            print("Coordinator continues while child runs:", child_id)
            yield ModelChunk(tool_call=ToolCall("status", {"child_id": child_id, "timeout": 2}))
        elif parent_steps == 3:
            print("Requested report:", json.loads(request.messages[-1].text)["report"]["text"])
            finish_work.set()
            yield ModelChunk(tool_call=ToolCall("wait", {"child_id": child_id, "timeout": 2}))
        else:
            yield ModelChunk(text="Coordinator received the review.")

    reviewer = Agent(model=CallbackModel(child_model), plugins=[ReviewTools()])
    coordinator = Agent(
        model=CallbackModel(coordinator_model),
        prompt=SystemPrompt.from_plugins(),
        plugins=[Subagents({"review": reviewer}), Approvals(allow=[Subagents.spawn])],
    )

    # Offline coordination only: let inspection finish once a real report is requested.
    def on_child_update(event: SubagentUpdatedEvent) -> None:
        report = event.runtime.report
        if report is not None and report.state is ChildReportState.PENDING:
            report_requested.set()

    async with coordinator.session() as session:
        session.on(SubagentUpdatedEvent, on_child_update)
        result = await session.run("Start the review and work independently; then ask for status.")
        print(result.text)
        print(session.children[0].status, session.children[0].text)


if __name__ == "__main__":
    asyncio.run(main())
