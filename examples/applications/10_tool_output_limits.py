"""Run with: uv run python -m examples.applications.10_tool_output_limits"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from harnessed import Agent, Context, Plugin, tool
from harnessed.events import ToolCallFinishedEvent
from harnessed.kernel import needs
from harnessed.models import Role, ToolResult, ToolStatus
from harnessed.plugins import LocalWorkspace
from harnessed.plugins.filesystem import Filesystem, FileSystemBackend
from harnessed.testing import ScriptedModel, call

MAX_TOOL_OUTPUT_CHARS = 1024


class LargeResultsTools(Plugin):
    fs = needs(FileSystemBackend)

    @tool
    async def cat_log(self) -> ToolResult:
        """Return a deliberately unbounded log, like cat without a range."""
        return ToolResult(await self.fs.read(Path("large.log")), ToolStatus.ERROR, exit_code=3)


tools = LargeResultsTools()


async def main():
    with TemporaryDirectory() as directory:
        source = "HEAD\n" + "x" * (2 * 1024 * 1024) + "\nTAIL ERROR"
        await asyncio.to_thread(Path(directory, "large.log").write_text, source, encoding="utf-8")
        model = ScriptedModel(
            [
                call(LargeResultsTools.cat_log),
                call(Filesystem.read_file, path="large.log", offset=0, limit=40),
                call(Filesystem.read_file, path="large.log", offset=40, limit=40),
                "Read bounded outputs and continued with explicit character ranges.",
            ]
        )
        agent = Agent(
            model=model,
            plugins=[
                LocalWorkspace(root=directory),
                Filesystem(),
                tools,
            ],
            context=Context(max_tool_output_chars=MAX_TOOL_OUTPUT_CHARS),
        )
        async with agent.session() as session:
            result = await session.run("Inspect the large log and read it in small ranges.")
            for event in result.events:
                if not isinstance(event, ToolCallFinishedEvent):
                    continue
                output = event.result
                if output.truncation is not None:
                    print(
                        f"Truncated {output.truncation.original_chars} to {len(output.content)} "
                        f"characters including notice; exit code {output.exit_code}."
                    )
                    print(output.content)
                else:
                    print(output.content)
            assert all(
                len(message.text) <= MAX_TOOL_OUTPUT_CHARS
                for request in model.requests
                for message in request.messages
                if message.role == Role.TOOL
            )
            print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
