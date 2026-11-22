"""Run with: uv run python -m examples.plugin_authoring.02_tool_context

A tool requests a workspace backend with ``needs`` and reports progress through
an injected ``ToolContext``. Injected parameters stay out of the model schema.
"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from harnessed import Agent, Inject, Plugin, ToolContext, needs, tool
from harnessed.events import ToolCallFinishedEvent, ToolProgressEvent
from harnessed.plugins import LocalWorkspace
from harnessed.plugins.filesystem import FileSystemBackend
from harnessed.testing import ScriptedModel, call


class WorkspaceTools(Plugin):
    filesystem = needs(FileSystemBackend)

    @tool(readonly=True, timeout_seconds=5)
    async def count_lines(self, path: str, call: Inject[ToolContext]) -> int:
        """Count the lines in a workspace file."""
        await call.progress(f"Reading {path}")
        return len((await self.filesystem.read(Path(path))).splitlines())


async def main():
    with TemporaryDirectory() as directory:
        await asyncio.to_thread(
            Path(directory, "notes.txt").write_text, "first\nsecond\nthird\n", encoding="utf-8"
        )
        # call() scripts a tool call from the decorated method, checking its arguments.
        model = ScriptedModel(
            [call(WorkspaceTools.count_lines, path="notes.txt"), "The file has three lines."]
        )
        agent = Agent(model=model, plugins=[LocalWorkspace(root=directory), WorkspaceTools()])
        async with agent.session() as session:
            async with session.stream("Count the lines in notes.txt.") as run_stream:
                async for event in run_stream:
                    match event:
                        case ToolProgressEvent(text=text):
                            print(f"Progress: {text}")
                        case ToolCallFinishedEvent(result=result):
                            print(f"Result ({result.status}): {result.content}")
            result = await run_stream.result()
            print(result.text)
        schema = next(
            tool.parameters for tool in model.requests[0].tools if tool.name == "count_lines"
        )
        assert list(schema["properties"]) == ["path"]
        print(f"Model arguments: {list(schema['properties'])}")


if __name__ == "__main__":
    asyncio.run(main())
