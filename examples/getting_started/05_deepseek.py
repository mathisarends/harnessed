"""Set DEEPSEEK_API_KEY; uv sync --extra openai; python -m examples.getting_started.05_deepseek."""

import asyncio
import os
from pathlib import Path

from harnessed import Agent, AgentLoop, Approvals, Instructions, SystemPrompt
from harnessed.events import ApprovalNeededEvent, TextDeltaEvent, ToolCallRequestedEvent
from harnessed.models import Decision
from harnessed.plugins import Filesystem, LocalWorkspace


async def main():
    workspace = Path.cwd()
    filesystem = Filesystem()
    agent = Agent(
        approvals=Approvals(default=Decision.ASK),
        model=f"deepseek:{os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')}",
        plugins=[LocalWorkspace(root=workspace), filesystem],
        loop=AgentLoop(max_steps=8),
        instructions=SystemPrompt(
            Instructions(text="Help the user understand this Python project. Answer in English."),
            Instructions.from_file(path=workspace / "AGENTS.md", missing_ok=True),
        ),
    )
    async with agent.session() as session:
        async with session.stream("Read pyproject.toml and explain the package.") as run_stream:
            async for event in run_stream:
                match event:
                    case TextDeltaEvent(text=text):
                        print(text, end="", flush=True)
                    case ToolCallRequestedEvent(call=call):
                        print(f"\n-> {call.name}")
                    case ApprovalNeededEvent(request_id=request_id, call=call):
                        answer = await asyncio.to_thread(input, f"Allow {call.name}? [y/N] ")
                        if answer.lower() == "y":
                            await session.approvals.allow(request_id)
                        else:
                            await session.approvals.deny(request_id)
        print()


if __name__ == "__main__":
    asyncio.run(main())
