"""Run with: uv run python -m examples.23_plugin_lifecycle"""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from harnessed import (
    Agent,
    ApprovalAnswer,
    Plugin,
    SystemPrompt,
    ToolCall,
    tool,
)
from harnessed.events import TurnEndedEvent
from harnessed.hooks import ExecuteTool, SessionAppended
from harnessed.kernel import Inject, PluginState, Scope, plugin
from harnessed.models import ModelChunk
from harnessed.plugins import Approvals
from harnessed.testing import ScriptedModel


class Notes(Protocol):
    @property
    def entries(self) -> list[str]: ...

    async def save(self, text: str) -> str: ...


class MemoryNotes:
    def __init__(self):
        self.entries: list[str] = []

    async def save(self, text: str) -> str:
        self.entries.append(text)
        return f"Saved note {len(self.entries)}"


@plugin()
async def notes_backend(scope: Scope):
    scope.provide(Notes, MemoryNotes())
    yield


@dataclass(frozen=True)
class NotesConfig:
    instruction: str = "Use save_note to save notes."


class NotesTools(Plugin, needs=(Notes,)):
    saved = PluginState[int](int)

    def __init__(self, config: NotesConfig):
        super().__init__()
        self._instruction = config.instruction

    def instructions(self) -> SystemPrompt:
        return SystemPrompt(self._instruction)

    @tool
    async def save_note(self, text: str, notes: Inject[Notes]) -> str:
        """Save a note."""
        result = await notes.save(text)
        self.saved += 1
        return result

    async def teardown(self):
        print(f"Notes plugin stopped after {self.saved} saves in this mount.")


@plugin()
async def trace(scope: Scope):
    @scope.on(ExecuteTool)
    async def around_tool(event, next_):
        print(f"before {event.call.name}")
        result = await next_()
        print(f"after {event.call.name}: {result.status}")
        return result

    @scope.on(SessionAppended)
    def record(event):
        if isinstance(event.event, TurnEndedEvent):
            print(f"{event.log.id}: turn ended at event {event.seq}")

    yield


async def main():
    notes = NotesTools(NotesConfig())
    harness = Agent(
        model=ScriptedModel(
            [
                [ModelChunk(tool_call=ToolCall("save_note", {"text": "Try Harnessed"}))],
                "The note has been saved.",
            ]
        ),
        plugins=[
            notes,
            notes_backend(),
            trace(),
            Approvals(approver=lambda request: ApprovalAnswer.ALLOW),
        ],
    )

    async with harness.session() as agent:
        print((await agent.run("Save a note.")).text)
        print(agent.scope[Notes].entries)


if __name__ == "__main__":
    asyncio.run(main())
