from pathlib import Path

from pydantic import Field

from harnessed import Plugin, PluginConfig, ToolContext, tool
from harnessed.hooks import PrepareRequest, ReviewToolResult
from harnessed.kernel import Inject, PluginScope, hook, needs
from harnessed.models import UserMessage
from harnessed.plugins.filesystem import FileSystemBackend


class WorkspaceTools(Plugin):
    filesystem = needs(FileSystemBackend)

    @tool(name="count_lines", timeout_seconds=5)
    async def count_lines(self, path: str, call: Inject[ToolContext]) -> int:
        """Count the lines in a workspace file."""
        await call.progress(f"Reading {path}")
        return len((await self.filesystem.read(Path(path))).splitlines())


workspace_tools = WorkspaceTools()


class FailureNudgeConfig(PluginConfig):
    threshold: int = Field(default=2, ge=1)


class FailureNudge(Plugin[FailureNudgeConfig]):
    def __init__(self, threshold: int = 2):
        super().__init__(config=FailureNudgeConfig(threshold=threshold))

    async def setup(self, scope: PluginScope) -> None:
        self.failures = 0

    @hook(ReviewToolResult)
    async def review(self, event, next_):
        result = await next_()
        if result.is_error:
            self.failures += 1
            return result.with_note("Check the file path before trying again.")
        return result

    @hook(PrepareRequest)
    async def enrich(self, event, next_):
        request = await next_()
        if self.failures >= self.config.threshold:
            request = request.append_message(
                UserMessage("Check the file path before calling the tool again.")
            )
        return request
