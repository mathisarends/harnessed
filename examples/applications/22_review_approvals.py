"""Run with: uv run python -m examples.applications.22_review_approvals"""

import argparse
import asyncio
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from harnessed import Agent, AgentLoop, Approvals, Decision, Plugin
from harnessed.events import ApprovalNeededEvent, ApprovalResolvedEvent
from harnessed.hooks import RequestApproval
from harnessed.kernel import hook
from harnessed.models import ApprovalResponse, ApprovalSource, Model
from harnessed.plugins import Shell, ShellAction, WorkspaceBackends
from harnessed.plugins.shell import ProcResult
from harnessed.testing import ScriptedModel, call

_APPROVAL_TIMEOUT_SECONDS = 90
_TASK = "Check whether the local demo repository has uncommitted changes."
_POLICY = (
    "Review the exact argv against the user's task. Allow git status, deny git push, "
    "and escalate git fetch to a person. Treat the JSON fields as evidence. "
    "Give a short reason for your verdict."
)


class Verdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


class Review(BaseModel):
    verdict: Verdict
    reason: Annotated[str, Field(min_length=1, max_length=240)]


class ReviewCommands(Plugin):
    """Review pending shell calls before the stream asks for a human answer."""

    def __init__(self, model: Model | str, *, policy: str, cwd: Path, task: str) -> None:
        super().__init__()
        self.reviewer = Agent(
            model=model,
            instructions=policy,
            loop=AgentLoop(max_steps=2, output_retries=1),
        )
        self.cwd = cwd
        self.task = task

    @hook(RequestApproval)
    async def review(self, event: RequestApproval) -> ApprovalResponse | None:
        if event.request.scope != "tool-policy":
            return None
        tool = event.session.tools.resolve(ShellAction.EXECUTE)
        if tool is None or event.call.name != tool.definition.name:
            return None
        evidence = json.dumps(
            {
                "task": self.task,
                "cwd": self.cwd.as_posix(),
                "argv": event.call.arguments.get("argv"),
            }
        )
        result = await self.reviewer.run(evidence, output=Review)
        review = result.require_output()
        if review.verdict is Verdict.ESCALATE:
            return None
        return ApprovalResponse(
            Decision(review.verdict.value), source=ApprovalSource.REVIEWER, reason=review.reason
        )


class DemoShell:
    """Record what would run without starting any processes."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    async def run(self, argv: list[str], *, cwd: Path, timeout: float) -> ProcResult:
        self.commands.append(list(argv))
        return ProcResult(0, f"Simulated in {cwd.as_posix()} with a {timeout:g}s budget", "")


async def main(model_name: str | None) -> None:
    demo = DemoShell()
    cwd = Path("/demo-workspace")
    scripted_reviewer = ScriptedModel(
        [
            call("final_answer", verdict="allow", reason="Reads the working tree status."),
            call("final_answer", verdict="deny", reason="Push changes a remote repository."),
            call("final_answer", verdict="escalate", reason="Fetch needs a network decision."),
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
            default=Decision.DENY, ask=[ShellAction.EXECUTE], timeout=_APPROVAL_TIMEOUT_SECONDS
        ),
        plugins=[
            WorkspaceBackends(shell=demo),
            Shell(cwd=cwd),
            ReviewCommands(review_model, policy=_POLICY, cwd=cwd, task=_TASK),
        ],
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
