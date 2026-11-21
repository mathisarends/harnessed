import asyncio

import pytest
from pydantic import BaseModel

from harnessed import Agent, Plugin, tool
from harnessed.events import PluginValueChangedEvent
from harnessed.sessions import SessionState, SessionValue
from harnessed.sessions.values import PluginValues
from harnessed.testing import ScriptedModel, call
from tests._audit import Audit


class Todo(BaseModel):
    title: str
    done: bool = False


class Handoff(BaseModel):
    summary: str
    next_step: str


class Todos(Plugin):
    items = SessionValue(list[Todo], key="tests.todos", default=[])
    handoff = SessionValue(Handoff, key="tests.handoff")

    @tool
    async def add_todo(self, title: str) -> str:
        """Add an open todo."""
        items = await self.items.update(lambda items: [*items, Todo(title=title)])
        return f"{len(items)} todos"

    @tool
    async def write_handoff(self, summary: str, next_step: str) -> str:
        """Record where the work stands."""
        await self.handoff.set(Handoff(summary=summary, next_step=next_step))
        return "recorded"


async def mounted(session) -> Todos:
    handle = await session.mount(Todos())
    assert isinstance(handle.instance, Todos)
    return handle.instance


async def test_values_are_typed_and_survive_serialized_restoration():
    model = ScriptedModel(
        [
            call("add_todo", title="Write tests"),
            call("write_handoff", summary="Tests drafted", next_step="Run them"),
            "done",
        ]
    )
    agent = Agent(model=model)
    async with agent.session() as session:
        plugin = await mounted(session)
        assert plugin.items.get() == []
        assert plugin.handoff.get() is None
        await session.run("Plan")
        state = SessionState.from_json(session.snapshot().to_json())
    assert state.plugin_values["tests.todos"] == ({"title": "Write tests", "done": False},)
    async with agent.session(state=state) as restored:
        plugin = await mounted(restored)
        assert plugin.items.get() == [Todo(title="Write tests")]
        assert plugin.handoff.get() == Handoff(summary="Tests drafted", next_step="Run them")


async def test_equal_values_append_nothing_and_updates_are_serialized():
    async with Agent(model=ScriptedModel([])).session() as session:
        audit = Audit(session)
        plugin = await mounted(session)

        async def add(title: str) -> None:
            await plugin.items.update(lambda items: [*items, Todo(title=title)])

        await asyncio.gather(*(add(str(index)) for index in range(5)))
        assert sorted(todo.title for todo in plugin.items.get()) == ["0", "1", "2", "3", "4"]
        await plugin.items.set(plugin.items.get())
        changes = [event for event in audit.events if isinstance(event, PluginValueChangedEvent)]
        assert len(changes) == 5


async def test_stored_values_that_no_longer_match_the_declared_type_fail_on_read():
    async with Agent(model=ScriptedModel([])).session() as session:
        plugin = await mounted(session)
        await session.scope[PluginValues].commit("tests.todos", [{"done": True}])
        with pytest.raises(ValueError, match=r"tests.todos"):
            plugin.items.get()


def test_declarations_require_a_key_and_a_mounted_plugin():
    with pytest.raises(ValueError, match="key"):
        SessionValue(str, key="")
    assert Todos.items.key == "tests.todos"
    with pytest.raises(RuntimeError, match="mounted plugin"):
        Todos().items.get()
