"""Run with: uv run python -m examples.02_plugins (offline, explicit composition)."""

import asyncio

from harnessed import Agent, Plugin, SystemPrompt, tool
from harnessed.models import ModelChunk, ToolCall
from harnessed.prompts import Instructions
from harnessed.testing import ScriptedModel


class Calculator(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt(Instructions(text="Use add for arithmetic."))

    @tool(readonly=True)
    async def add(self, a: int, b: int) -> int:
        """Add two integers."""
        return a + b


async def main():
    model = ScriptedModel(
        [
            [ModelChunk(tool_call=ToolCall("add", {"a": 19, "b": 23}))],
            "The result is 42.",
        ]
    )
    agent = Agent(model=model, instructions="Keep answers concise.", plugins=[Calculator()])
    result = await agent.run("What is 19 + 23?")
    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
