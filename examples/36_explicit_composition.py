"""Run with: uv run python -m examples.36_explicit_composition"""

import asyncio

from harnessed import Agent, AgentLoop, Plugin, SystemPrompt, tool
from harnessed.errors import PluginConflict
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


def text_plugins(*, prefix: str = "Library", debug: bool = False) -> list[Plugin]:
    """Share a recipe as a function returning a plain list; parameters select variants."""
    return [TextTools(prefix=prefix), *([DebugTools()] if debug else [])]


async def main() -> None:
    agent = Agent(
        model=ScriptedModel([call("label", text="Hello"), "Application: Hello"]),
        plugins=text_plugins(prefix="Application"),
        instructions="Help with text.",
        loop=AgentLoop(max_steps=6),
    )
    print(agent.describe())
    print(agent.loop)
    print((await agent.run("Label Hello")).text)
    try:
        Agent(plugins=[*text_plugins(), TextTools(prefix="Other")])
    except PluginConflict as error:
        print(f"Conflict: {error}")


if __name__ == "__main__":
    asyncio.run(main())
