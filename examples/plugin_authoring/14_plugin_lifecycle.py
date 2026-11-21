"""Run with: uv run python -m examples.plugin_authoring.14_plugin_lifecycle"""

import asyncio
from dataclasses import dataclass
from typing import Protocol, override

from harnessed import Agent, Approvals, Plugin, SystemPrompt, tool
from harnessed.events import TurnEndedEvent
from harnessed.hooks import ExecuteTool
from harnessed.kernel import PluginScope, PluginState, needs
from harnessed.models import Decision, ModelChunk, ToolCall
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


class NotesBackend(Plugin):
    async def setup(self, scope: PluginScope) -> None:
        scope.provide(Notes, MemoryNotes())


@dataclass(frozen=True)
class NotesConfig:
    instruction: str = "Use save_note to save notes."


class NotesTools(Plugin):
    notes = needs(Notes)

    saved = PluginState[int](int)

    def __init__(self, config: NotesConfig):
        super().__init__()
        self._instruction = config.instruction

    def instructions(self) -> SystemPrompt:
        return SystemPrompt(self._instruction)

    @tool
    async def save_note(self, text: str) -> str:
        """Save a note."""
        result = await self.notes.save(text)
        self.saved += 1
        return result

    @override
    async def teardown(self, scope: PluginScope) -> None:
        print(f"Notes plugin stopped after {self.saved} saves in this mount.")


class Trace(Plugin):
    async def setup(self, scope: PluginScope) -> None:
        @scope.on(ExecuteTool)
        async def around_tool(event, next_):
            print(f"before {event.call.name}")
            result = await next_()
            print(f"after {event.call.name}: {result.status}")
            return result

        # Committed durable events are hooks themselves; no wrapper event is needed.
        @scope.on(TurnEndedEvent)
        def record(event: TurnEndedEvent):
            print(f"turn {event.turn_id} ended: {event.reason}")


async def main():
    notes = NotesTools(NotesConfig())
    harness = Agent(
        model=ScriptedModel(
            [
                [ModelChunk(tool_call=ToolCall("save_note", {"text": "Try Harnessed"}))],
                "The note has been saved.",
            ]
        ),
        approvals=Approvals(default=Decision.ASK, approver=lambda _: Decision.ALLOW),
        plugins=[notes, NotesBackend(), Trace()],
    )

    async with harness.session() as agent:
        print((await agent.run("Save a note.")).text)
        print(agent.scope[Notes].entries)


if __name__ == "__main__":
    asyncio.run(main())
