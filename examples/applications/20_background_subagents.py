"""Run with: uv run python -m examples.applications.20_background_subagents"""

import asyncio
import json

from harnessed import Agent, Approvals, Plugin, tool
from harnessed.events import SubagentUpdatedEvent
from harnessed.models import ModelChunk, ModelRequest, ToolCall
from harnessed.plugins import SubagentAction, Subagents
from harnessed.plugins.subagents import ChildReportState
from harnessed.testing import CallbackModel


def _report_requested(event: object) -> bool:
    if not isinstance(event, SubagentUpdatedEvent):
        return False
    report = event.runtime.report
    return report is not None and report.state is ChildReportState.PENDING


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
            yield ModelChunk(
                tool_call=ToolCall("status", {"child_id": child_id, "timeout_seconds": 2})
            )
        elif parent_steps == 3:
            print("Requested report:", json.loads(request.messages[-1].text)["report"]["text"])
            finish_work.set()
            yield ModelChunk(
                tool_call=ToolCall("wait", {"child_id": child_id, "timeout_seconds": 2})
            )
        else:
            yield ModelChunk(text="Coordinator received the review.")

    reviewer = Agent(model=CallbackModel(child_model), plugins=[ReviewTools()])
    coordinator = Agent(
        model=CallbackModel(coordinator_model),
        approvals=Approvals(
            allow=[SubagentAction.SPAWN, SubagentAction.STATUS, SubagentAction.WAIT]
        ),
        plugins=[Subagents({"review": reviewer})],
    )

    async with (
        coordinator.session() as session,
        session.stream("Start the review and work independently; then ask for status.") as run,
    ):
        # Offline coordination only: let inspection finish once a real report is requested.
        async for event in run:
            if _report_requested(event):
                report_requested.set()
        result = await run.result()
        print(result.text)
        print(session.children[0].status, session.children[0].text)


if __name__ == "__main__":
    asyncio.run(main())
