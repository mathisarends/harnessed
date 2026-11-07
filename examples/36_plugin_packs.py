"""Run with: uv run python -m examples.36_plugin_packs"""

import asyncio

from harnessed import (
    Agent,
    Plugin,
    PluginPack,
    SystemPrompt,
    tool,
)
from harnessed.plugins import AgentLoop
from harnessed.testing import ScriptedModel, call


class TextTools(Plugin, slot="example.text"):
    def __init__(self, prefix: str = "Library") -> None:
        super().__init__()
        self.prefix = prefix

    def instructions(self) -> SystemPrompt:
        return SystemPrompt(f"Use label to format text with the {self.prefix!r} prefix.")

    @tool(readonly=True)
    def label(self, text: str) -> str:
        return f"{self.prefix}: {text}"


class DebugTools(Plugin):
    @tool(readonly=True)
    def debug(self) -> str:
        return "Debugging enabled."


def library_pack() -> PluginPack:
    return PluginPack(
        "library",
        [TextTools(), DebugTools(), AgentLoop(max_steps=12)],
    )


async def main() -> None:
    project = PluginPack("project", [library_pack(), AgentLoop(max_steps=6)]).without(DebugTools)
    agent = Agent(
        model=ScriptedModel([call("label", text="Hello"), "Application: Hello"]),
        plugins=[project, TextTools(prefix="Application")],
        instructions="Help with text.",
    )
    print(agent.describe())
    print((await agent.run("Label Hello")).text)


if __name__ == "__main__":
    asyncio.run(main())
