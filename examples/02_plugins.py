"""Run with: uv run python -m examples.02_plugins (offline, explicit composition)."""

import asyncio

from harnessed import (
    Agent,
    Instructions,
    Plugin,
    SystemPrompt,
    ToolCall,
    tool,
)
from harnessed.models import Model, ModelChunk
from harnessed.plugins import Service
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
    # Equivalent to supplying model=... on Agent; the prompt is part of the core.
    calculator = Calculator()
    agent = Agent(
        prompt=SystemPrompt.from_plugins(Instructions(text="Keep answers concise.")),
        plugins=[Service(Model, model), calculator],
    )
    print("Composition:", [type(value).__name__ for value in agent.plugins])
    print(agent.prompt)
    async with agent.session() as session:
        print((await session.run("What is 19 + 23?")).text)
        print("Exact prompt:", await session.prompt.render())


if __name__ == "__main__":
    asyncio.run(main())
