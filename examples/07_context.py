"""Run with: uv run python -m examples.07_context"""

import asyncio
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from harnessed import (
    Agent,
    File,
    Image,
    Instructions,
    Plugin,
    SystemPrompt,
    ToolCall,
    tool,
)
from harnessed.hooks import PrepareRequest
from harnessed.kernel import Inject, Scope, hook, plugin
from harnessed.models import ModelChunk, UserMessage
from harnessed.testing import ScriptedModel


class Memory(Protocol):
    async def search(self, query: str, k: int) -> list[str]: ...


class DemoMemory:
    async def search(self, query: str, k: int) -> list[str]:
        return ["The project uses Python 3.12+.", "Focus on authentication tests."][:k]


@plugin
async def memory_backend(scope: Scope):
    scope.provide(Memory, DemoMemory())
    yield


class RelevantMemories(Plugin, needs=(Memory,)):
    @hook(PrepareRequest)
    async def recall(self, event: PrepareRequest, next_, memory: Inject[Memory]):
        request = await next_()
        query = next(
            (
                message.text
                for message in reversed(request.messages)
                if isinstance(message, UserMessage)
            ),
            "project",
        )
        hits = await memory.search(query, k=3)
        for hit in hits:
            request = request.append_message(UserMessage(hit))
        return request


class MemoryTools(Plugin):
    needs = (Memory,)

    @tool(readonly=True)
    async def recall(self, query: str, memory: Inject[Memory]) -> list[str]:
        """Search long-term memory for relevant project information."""
        return await memory.search(query, k=5)


memory_tools = MemoryTools()


async def main():
    with TemporaryDirectory(prefix="harnessed-context-") as directory:
        root = Path(directory)
        (root / "AGENTS.md").write_text("Keep changes focused.", encoding="utf-8")
        (root / "PR.diff").write_text("+ def test_login(): ...", encoding="utf-8")
        (root / "screenshot.png").write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
            )
        )
        model = ScriptedModel(
            [
                [ModelChunk(tool_call=ToolCall("recall", {"query": "authentication"}))],
                "Reviewed with task context, attachments and memory.",
                "A new task retains the earlier user input as conversation history.",
            ]
        )
        harness = Agent(
            model=model,
            prompt=SystemPrompt.from_plugins(
                Instructions(text="You are a code reviewer."),
                Instructions.from_file(path=root / "AGENTS.md"),
            ),
            plugins=[
                memory_backend(),
                RelevantMemories(),
                memory_tools,
            ],
        )
        async with harness.session() as agent:
            result = await agent.run(
                "Review this PR.\n\nTicket: Fix the authentication regression.",
                attachments=[File(root / "PR.diff"), Image(root / "screenshot.png")],
            )
            print(result.text)
            for message in model.requests[0].messages:
                print(f"{message.role}: {message.text[:160]}")
            attached = next(m for m in model.requests[0].messages if "File: PR.diff" in m.text)
            print("Image part:", type(attached.content[-1]).__name__)

            await agent.run("Now discuss a different task.")
            print(
                "Ticket input in next turn:",
                any("Ticket:" in m.text for m in model.requests[-1].messages),
            )
            print(
                "Request-only memory in retained messages:",
                any("Python 3.12" in m.text for m in agent.messages),
            )


if __name__ == "__main__":
    asyncio.run(main())
