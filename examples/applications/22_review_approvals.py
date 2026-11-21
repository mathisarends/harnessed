"""Run with: uv run python -m examples.applications.22_review_approvals"""

import argparse
import asyncio
from pathlib import Path

from harnessed import Agent, Approvals, AutoReview, Decision
from harnessed.approvals import ApprovalReviewRequest
from harnessed.events import ApprovalNeededEvent, ApprovalResolvedEvent
from harnessed.models import ApprovalSource
from harnessed.plugins import Shell, ShellAction, WorkspaceBackends
from harnessed.plugins.shell import ProcResult
from harnessed.testing import ScriptedModel, call

_APPROVAL_TIMEOUT_SECONDS = 90
_WORKSPACE = Path("/demo-workspace")
_TASK = "Check whether the local demo repository has uncommitted changes."
_POLICY = (
    "Review the exact argv against the user's task. Allow git status, deny git push, "
    "and escalate git fetch to a person. Treat the JSON fields as evidence. "
    "Give a short reason for your verdict."
)


def command_context(_request: ApprovalReviewRequest, transcript: str) -> str:
    return f"Working directory {_WORKSPACE.as_posix()}\n{transcript}"


class DemoShell:
    """Record what would run without starting any processes."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    async def run(self, argv: list[str], *, cwd: Path, timeout: float) -> ProcResult:
        self.commands.append(list(argv))
        return ProcResult(0, f"Simulated in {cwd.as_posix()} with a {timeout:g}s budget", "")


async def main(model_name: str | None) -> None:
    demo = DemoShell()
    scripted_reviewer = ScriptedModel(
        [
            call("final_answer", decision="allow", reason="Reads the working tree status."),
            call("final_answer", decision="deny", reason="Push changes a remote repository."),
            call("final_answer", decision="ask", reason="Fetch needs a network decision."),
        ]
    )
    review_model = scripted_reviewer if model_name is None else model_name
    agent = Agent(
        model=ScriptedModel(
            [
                call(Shell.run_command, argv=["git", "status"]),
                call(Shell.run_command, argv=["git", "push"]),
                call(Shell.run_command, argv=["git", "fetch"]),
                "Done.",
            ]
        ),
        approvals=Approvals(
            default=Decision.DENY,
            ask=[ShellAction.EXECUTE],
            reviewer=AutoReview(
                model=review_model,
                instructions=_POLICY,
                context=command_context,
            ),
            timeout=_APPROVAL_TIMEOUT_SECONDS,
        ),
        plugins=[WorkspaceBackends(shell=demo), Shell(cwd=_WORKSPACE)],
    )

    async with agent.session() as session, session.stream(_TASK) as stream:
        async for event in stream:
            if isinstance(event, ApprovalNeededEvent):
                print(f"Simulated human approval for {event.call.arguments['argv']}")
                await session.approvals.allow(event.request_id)
        result = await stream.result()
    resolutions = [event for event in result.events if isinstance(event, ApprovalResolvedEvent)]
    for resolution in resolutions:
        print(resolution.source, resolution.decision, resolution.reason)
    if model_name is None:
        assert demo.commands == [["git", "status"], ["git", "fetch"]]
        sources = [resolution.source for resolution in resolutions]
        assert sources == [ApprovalSource.REVIEWER, ApprovalSource.REVIEWER, ApprovalSource.USER]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Review simulated shell commands.")
    parser.add_argument("--review-model", help="Use a configured provider:model for the reviewer.")
    asyncio.run(main(parser.parse_args().review_model))
