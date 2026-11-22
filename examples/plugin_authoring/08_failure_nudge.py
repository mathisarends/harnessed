"""Run with: uv run python -m examples.plugin_authoring.08_failure_nudge

One plugin cooperates across two hooks: ``ReviewToolResult`` counts failed tool
calls and annotates them, and ``PrepareRequest`` adds a reminder to the next model
request once a configured threshold is reached.
"""

import asyncio
from typing import override

from pydantic import Field

from harnessed import Agent, Plugin, PluginConfig, PluginScope, hook, tool
from harnessed.hooks import PrepareRequest, ReviewToolResult
from harnessed.models import ToolResult, ToolStatus, UserMessage
from harnessed.testing import ScriptedModel, call

NUDGE = "Check the file path before calling the tool again."


class Files(Plugin):
    @tool(readonly=True)
    async def count_lines(self, path: str) -> ToolResult:
        """Count the lines in a workspace file."""
        return ToolResult(f"{path} does not exist.", ToolStatus.ERROR)


class FailureNudgeConfig(PluginConfig):
    threshold: int = Field(default=2, ge=1)


class FailureNudge(Plugin[FailureNudgeConfig]):
    def __init__(self, threshold: int = 2):
        super().__init__(config=FailureNudgeConfig(threshold=threshold))

    @override
    async def setup(self, scope: PluginScope) -> None:
        self.failures = 0

    @hook(ReviewToolResult)
    async def review(self, _event, next_):
        result = await next_()
        if result.is_error:
            self.failures += 1
            return result.with_note("Check the file path before trying again.")
        return result

    @hook(PrepareRequest)
    async def enrich(self, _event, next_):
        request = await next_()
        if self.failures >= self.config.threshold:
            request = request.append_message(UserMessage(NUDGE))
        return request


async def main():
    model = ScriptedModel(
        [
            call(Files.count_lines, path="missing.txt"),
            call(Files.count_lines, path="missing.txt"),
            "The file is missing. I will check the path first.",
        ]
    )
    agent = Agent(model=model, plugins=[Files(), FailureNudge(threshold=2)])
    result = await agent.run("Count the lines in missing.txt.")
    print(result.text)
    nudges = [m.text for m in model.requests[-1].messages if m.text == NUDGE]
    assert nudges == [NUDGE]
    print("Nudge in the final request:", nudges)


if __name__ == "__main__":
    asyncio.run(main())
