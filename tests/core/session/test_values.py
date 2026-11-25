import asyncio
from datetime import date
from typing import Annotated

import pytest
from pydantic import BaseModel, PositiveInt, Strict

from harnessed import Agent, Plugin, tool
from harnessed.events import PluginValueChangedEvent
from harnessed.kernel import hook
from harnessed.sessions import SessionState, SessionValue
from harnessed.testing import ScriptedModel, call
from tests._audit import Audit
from tests._commits import CommitRecorder


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
        state = SessionState.from_json(session.state().to_json())
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
    state = SessionState(plugin_values={"tests.todos": [{"done": True}]})
    async with Agent(model=ScriptedModel([])).session(state=state) as session:
        plugin = await mounted(session)
        with pytest.raises(ValueError, match=r"tests.todos"):
            plugin.items.get()


async def test_failed_in_place_update_leaves_each_sessions_default_unchanged():
    def fail(items: list[Todo]) -> list[Todo]:
        items.append(Todo(title="Uncommitted"))
        raise RuntimeError("update failed")

    agent = Agent(model=ScriptedModel([]))
    async with agent.session() as first:
        plugin = await mounted(first)
        with pytest.raises(RuntimeError, match="update failed"):
            await plugin.items.update(fail)
        assert plugin.items.get() == []
        assert first.state().plugin_values == {}
    async with agent.session() as second:
        plugin = await mounted(second)
        assert plugin.items.get() == []


async def test_invalid_writes_leave_the_last_good_value_and_emit_no_event():
    class Counter(Plugin, provides=True):
        count = SessionValue(PositiveInt, key="tests.count", default=1)

    agent = Agent(model=ScriptedModel([]), plugins=[Counter()])
    async with agent.session() as session:
        audit = Audit(session)
        plugin = session.scope[Counter]
        await plugin.count.set(2)
        with pytest.raises(ValueError, match=r"tests.count"):
            await plugin.count.set(0)
        with pytest.raises(ValueError, match=r"tests.count"):
            await plugin.count.update(lambda count: -count)
        assert plugin.count.get() == 2
        changes = [event for event in audit.events if isinstance(event, PluginValueChangedEvent)]
        assert [event.value for event in changes] == [2]


async def test_value_schema_uses_json_validation_after_restoration():
    class Calendar(Plugin, provides=True):
        day = SessionValue(Annotated[date, Strict()], key="tests.day")

    agent = Agent(model=ScriptedModel([]), plugins=[Calendar()])
    selected = date(2026, 11, 21)
    async with agent.session() as session:
        await session.scope[Calendar].day.set(selected)
        state = SessionState.from_json(session.state().to_json())
    async with agent.session(state=state) as session:
        assert session.scope[Calendar].day.get() == selected


async def test_value_observer_can_commit_another_value():
    class Mirror(Plugin, provides=True):
        original = SessionValue(int, key="tests.original", default=0)
        mirrored = SessionValue(int, key="tests.mirrored", default=0)

        @hook(PluginValueChangedEvent)
        async def on_change(self, event: PluginValueChangedEvent) -> None:
            if event.key == "tests.original":
                await self.mirrored.set(self.original.get())

    store = CommitRecorder(documents=True)
    agent = Agent(model=ScriptedModel([]), plugins=[Mirror()])
    async with agent.session(id="mirrored", store=store) as session:
        plugin = session.scope[Mirror]
        deadline_seconds = 3
        async with asyncio.timeout(deadline_seconds):
            await plugin.original.set(42)
        assert plugin.mirrored.get() == 42
    async with agent.session(state=store.latest("mirrored")) as session:
        assert session.scope[Mirror].mirrored.get() == 42


def test_declarations_require_a_key_and_a_mounted_plugin():
    with pytest.raises(ValueError, match="key"):
        SessionValue(str, key="")
    assert Todos.items.key == "tests.todos"
    with pytest.raises(RuntimeError, match="mounted plugin"):
        Todos().items.get()
