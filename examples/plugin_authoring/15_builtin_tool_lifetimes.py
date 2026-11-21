"""Run with: uv run python -m examples.plugin_authoring.15_builtin_tool_lifetimes"""

import asyncio
from pathlib import Path
from typing import override

from harnessed import Agent, PluginValidation
from harnessed.models import ModelChunk, ToolCall
from harnessed.plugins import WorkspaceBackends
from harnessed.plugins.shell import ProcResult, Shell, ShellBackend
from harnessed.testing import ScriptedModel


class DemoShell(ShellBackend):
    def __init__(self, name: str):
        self.name = name

    @override
    async def run(self, argv: list[str], *, cwd: Path, timeout_seconds: float) -> ProcResult:
        return ProcResult(0, f"{self.name}: {' '.join(argv)}", "")


async def main():
    model = ScriptedModel(
        [
            [ModelChunk(tool_call=ToolCall("run_command", {"argv": ["echo", "hello"]}))],
            "The first backend handled the command.",
            [ModelChunk(tool_call=ToolCall("run_command", {"argv": ["echo", "again"]}))],
            "The replacement backend handled the command.",
        ]
    )
    # This example deliberately opens before adding the shell backend at runtime.
    agent = Agent(
        model=model,
        plugin_validation=PluginValidation.WARN,
        plugins=[Shell()],
    )
    outputs = []
    async with agent.session() as session:
        tools = session.tools
        assert tools.names == frozenset()
        print("Before a backend: no shell tools")
        first = await session.mount(WorkspaceBackends(shell=DemoShell("local")))
        assert list(tools) == ["run_command"]
        first_result = await session.run("Say hello using the shell.")
        print(first_result.text)
        outputs.extend(result.content for _, result in first_result.tool_calls)
        await first.aclose()
        assert tools.names == frozenset()
        print("Backend removed: no shell tools")
        await session.mount(WorkspaceBackends(shell=DemoShell("remote")))
        assert list(tools) == ["run_command"]
        second_result = await session.run("Run another command.")
        print(second_result.text)
        outputs.extend(result.content for _, result in second_result.tool_calls)
        assert len(outputs) == 2 and "local:" in outputs[0] and ("remote:" in outputs[1])
        print("Recorded outputs:", *outputs, sep="\n")


if __name__ == "__main__":
    asyncio.run(main())
