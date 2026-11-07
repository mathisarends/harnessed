"""Run with: uv run python -m examples.05_tools_and_injection"""

import asyncio

from examples.features.workspace import FailureNudge, WorkspaceTools
from harnessed import (
    Agent,
    ApprovalAnswer,
    PolicyDecision,
)
from harnessed.events import ToolCallFinishedEvent, ToolProgressEvent
from harnessed.plugins import Approvals, Filesystem
from harnessed.testing import ScriptedModel, call


async def main():

    async def approve(request):
        print(f"Approval: allow {request.call.name} for the rest of this session")
        return ApprovalAnswer.ALWAYS

    model = ScriptedModel(
        [
            call(WorkspaceTools.count_lines, path="missing-example-file.txt"),
            call(WorkspaceTools.count_lines, path="missing-example-file.txt"),
            "The file is missing. I will check the path first.",
        ]
    )
    filesystem = Filesystem(root=".")
    agent = Agent(
        model=model,
        plugins=[
            filesystem,
            WorkspaceTools(),
            FailureNudge(threshold=2),
            Approvals(
                default=PolicyDecision.ALLOW,
                ask=[WorkspaceTools.count_lines],
                approver=approve,
            ),
        ],
    )
    async with agent.session() as session:
        async with session.stream("Count the lines in the example file.") as run_stream:
            async for event in run_stream:
                match event:
                    case ToolProgressEvent(text=text):
                        print(f"Progress: {text}")
                    case ToolCallFinishedEvent(result=result):
                        print(f"Result ({result.status}): {result.content}")
        schema = next(
            tool.parameters for tool in model.requests[0].tools if tool.name == "count_lines"
        )
        print(f"Model arguments: {list(schema['properties'])}")
        final_request = model.requests[-1]
        print("\nNudge in next request:")
        print([m.text for m in final_request.messages if "workspace-path" in m.text])


if __name__ == "__main__":
    asyncio.run(main())
