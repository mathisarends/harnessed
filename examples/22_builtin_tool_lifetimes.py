"""Run with: uv run python -m examples.22_builtin_tool_lifetimes"""

import asyncio
from pathlib import Path
from typing import override

from harnessed import Agent, Plugin, PluginValidation
from harnessed.kernel import PluginScope
from harnessed.models import ModelChunk, ToolCall
from harnessed.plugins.shell import ProcResult, ShellTools
from harnessed.plugins.shell import ShellBackend as Shell
from harnessed.testing import ScriptedModel


class DemoShell(Shell):
    def __init__(self, name: str):
        self.name = name

    @override
    async def run(self, argv: list[str], *, cwd: Path, timeout: float) -> ProcResult:
        return ProcResult(0, f"{self.name}: {' '.join(argv)}", "")


class ShellBackend(Plugin):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    async def setup(self, scope: PluginScope) -> None:
        scope.provide(Shell, DemoShell(self.name))


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
        plugins=[ShellTools()],
    )
    outputs = []
    async with agent.session() as session:
        tools = session.tools
        assert tools.names == frozenset()
        print("Before a backend: no shell tools")
        first = await session.mount(ShellBackend("local"))
        assert list(tools) == ["run_command"]
        first_result = await session.run("Say hello using the shell.")
        print(first_result.text)
        outputs.extend(result.content for _, result in first_result.tool_calls)
        await first.aclose()
        assert tools.names == frozenset()
        print("Backend removed: no shell tools")
        await session.mount(ShellBackend("remote"))
        assert list(tools) == ["run_command"]
        second_result = await session.run("Run another command.")
        print(second_result.text)
        outputs.extend(result.content for _, result in second_result.tool_calls)
        assert len(outputs) == 2 and "local:" in outputs[0] and ("remote:" in outputs[1])
        print("Recorded outputs:", *outputs, sep="\n")


if __name__ == "__main__":
    asyncio.run(main())
