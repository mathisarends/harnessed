import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from pydantic import PydanticSchemaGenerationError

from harnessed import Agent as Application
from harnessed import Plugin, Session, ToolCall, hook, plugin, tool
from harnessed.core.prompt.system_prompt import SystemPromptStore
from harnessed.core.runtime.services import RuntimeServices
from harnessed.core.tools.registry import ToolRegistry
from harnessed.kernel import Emit, Inject, PluginStatus, Scope, ScopeClosed
from harnessed.plugins import Approvals
from harnessed.testing import ScriptedModel


@dataclass
class Backend:
    name: str


@dataclass
class Bridge:
    name: str


class Ready:
    pass


class Ping(Emit):
    pass


async def test_direct_registration_disposers_are_idempotent_and_restore_overrides():
    disposers, seen = ([], [])

    async def base() -> str:
        return "base"

    @plugin(needs=[ToolRegistry, SystemPromptStore])
    async def feature(scope):

        async def override() -> str:
            return "override"

        disposers.extend(
            [
                scope.provide(str, "override"),
                scope.add_listener(Ping, lambda event: seen.append("override")),
                scope[ToolRegistry].register(override, name="read", override=True),
                scope[SystemPromptStore].fixed("override"),
            ]
        )
        yield

    async with Scope() as scope:
        await scope.install([RuntimeServices()])
        scope.provide(str, "base")
        scope.add_listener(Ping, lambda event: seen.append("base"))
        scope[ToolRegistry].register(base, name="read")
        scope[SystemPromptStore].fixed("base")
        handle = await scope.plugin(feature())
        assert scope[str] == "override"
        assert (await scope[ToolRegistry].execute(ToolCall("read"))).content == "override"
        assert await scope[SystemPromptStore].render() == "override"
        await scope.dispatch(Ping())
        assert seen == ["base", "override"]
        for dispose in disposers:
            await dispose()
            await dispose()
        assert scope[str] == "base"
        assert (await scope[ToolRegistry].execute(ToolCall("read"))).content == "base"
        assert await scope[SystemPromptStore].render() == "base"
        await scope.dispatch(Ping())
        assert seen == ["base", "override", "base"]
        await handle.aclose()
        assert scope[str] == "base" and await scope[SystemPromptStore].render() == "base"


async def test_registrations_are_hidden_during_setup_and_before_async_teardown_finishes():
    starting, ready = (asyncio.Event(), asyncio.Event())
    stopping, release = (asyncio.Event(), asyncio.Event())
    seen = []

    async def base() -> str:
        return "base"

    @plugin(needs=[ToolRegistry, SystemPromptStore])
    async def feature(scope):

        @scope[ToolRegistry].tool(name="read", override=True)
        async def override() -> str:
            return "override"

        scope[SystemPromptStore].fixed("override")
        scope.on(Ping, lambda event: seen.append("override"))
        starting.set()
        await ready.wait()
        yield
        stopping.set()
        await release.wait()

    async with Scope() as scope:
        await scope.install([RuntimeServices()])
        scope[ToolRegistry].register(base, name="read")
        scope[SystemPromptStore].fixed("base")
        mounting = asyncio.create_task(scope.plugin(feature()))
        try:
            await asyncio.wait_for(starting.wait(), timeout=5)
            assert (await scope[ToolRegistry].execute(ToolCall("read"))).content == "base"
            assert await scope[SystemPromptStore].render() == "base"
            await scope.dispatch(Ping())
            assert seen == []
        finally:
            ready.set()
            handle = await asyncio.wait_for(mounting, timeout=5)
        assert (await scope[ToolRegistry].execute(ToolCall("read"))).content == "override"
        assert await scope[SystemPromptStore].render() == "override"
        await scope.dispatch(Ping())
        closing = asyncio.create_task(handle.aclose())
        try:
            await asyncio.wait_for(stopping.wait(), timeout=5)
            assert not closing.done()
            assert (await scope[ToolRegistry].execute(ToolCall("read"))).content == "base"
            assert await scope[SystemPromptStore].render() == "base"
            await scope.dispatch(Ping())
            assert seen == ["override"]
        finally:
            release.set()
            await asyncio.wait_for(closing, timeout=5)


async def test_transitive_needs_rebuild_all_registrations_without_duplicates():
    lifecycle, seen = ([], [])

    @plugin
    async def provider(scope, name):
        scope.provide(Backend, Backend(name))
        yield
        lifecycle.append(("provider", name))

    @plugin(needs=[Backend])
    async def bridge(scope):
        name = scope[Backend].name
        scope.provide(Bridge, Bridge(name))
        yield
        lifecycle.append(("bridge", name))

    class Feature(Plugin):
        needs = (Bridge, ToolRegistry, SystemPromptStore)

        async def setup(self, scope):
            self.backend = scope[Bridge].name
            self.calls = 0
            scope[SystemPromptStore].fixed(self.backend)
            scope.provide(Ready, Ready())

        @tool
        async def read(self) -> str:
            self.calls += 1
            return f"{self.backend}:{self.calls}"

        @hook(Ping)
        def ping(self, event):
            seen.append(self.backend)

        async def teardown(self, scope):
            assert scope[Bridge].name == self.backend
            lifecycle.append(("feature", self.backend))

    async with Scope() as scope:
        _, bridge_handle, feature = await scope.install([RuntimeServices(), bridge(), Feature()])
        for name in ("one", "two", "three"):
            source = await scope.plugin(provider(name))
            await scope.settle()
            assert feature.status == bridge_handle.status == PluginStatus.ACTIVE
            assert [definition.name for definition in scope[ToolRegistry].definitions] == ["read"]
            assert (await scope[ToolRegistry].execute(ToolCall("read"))).content == f"{name}:1"
            assert await scope[SystemPromptStore].render() == name
            assert Ready in scope
            await scope.dispatch(Ping())
            await source.aclose()
            await scope.settle()
            assert feature.status == bridge_handle.status == PluginStatus.WAITING
            assert scope[ToolRegistry].definitions == ()
            assert await scope[SystemPromptStore].render() == ""
            assert Ready not in scope
            await scope.dispatch(Ping())
            assert lifecycle[-3:] == [("feature", name), ("bridge", name), ("provider", name)]
        assert seen == ["one", "two", "three"]
        await feature.aclose()
        await scope.plugin(provider("four"))
        await scope.settle()
        assert feature.status == PluginStatus.DISPOSED and scope[ToolRegistry].definitions == ()


async def test_registration_failure_after_class_setup_runs_teardown_and_rolls_back():
    cleaned = []

    class Broken(Plugin):
        needs = (ToolRegistry, SystemPromptStore)

        async def setup(self, scope):
            scope[SystemPromptStore].fixed("must disappear")
            scope.provide(Ready, Ready())

        @hook(Ping)
        def observe(self, event):
            pytest.fail("A failed mount left its listener registered")

        @tool
        async def valid(self) -> str:
            return "must disappear"

        @tool
        async def invalid(self, value: Ready) -> str:
            return str(value)

        async def teardown(self, scope):
            cleaned.append("closed")

    async with Scope() as scope:
        await scope.install([RuntimeServices()])
        with pytest.raises(PydanticSchemaGenerationError, match="Ready"):
            await scope.plugin(Broken())
        assert cleaned == ["closed"]
        assert scope[ToolRegistry].definitions == () and Ready not in scope
        assert await scope[SystemPromptStore].render() == ""
        await scope.dispatch(Ping())


async def test_teardown_errors_remove_all_effects_and_allow_fresh_activation():
    seen = []

    @plugin(needs=[Backend, ToolRegistry, SystemPromptStore])
    async def feature(scope):
        name = scope[Backend].name
        scope.provide(Ready, Ready())
        scope[SystemPromptStore].fixed(name)
        scope.on(Ping, lambda event: seen.append(name))

        @scope[ToolRegistry].tool
        async def read() -> str:
            return name

        yield
        if name == "broken":
            raise ValueError("teardown failed")

    async with Scope() as scope:
        await scope.install([RuntimeServices()])
        remove = scope.provide(Backend, Backend("broken"))
        handle = await scope.plugin(feature())
        await remove()
        with pytest.raises(ExceptionGroup, match="composition failed"):
            await scope.settle()
        assert handle.status == PluginStatus.WAITING and Ready not in scope
        assert (
            scope[ToolRegistry].definitions == () and await scope[SystemPromptStore].render() == ""
        )
        await scope.dispatch(Ping())
        scope.provide(Backend, Backend("fixed"))
        await scope.settle()
        assert handle.status == PluginStatus.ACTIVE and Ready in scope
        assert (await scope[ToolRegistry].execute(ToolCall("read"))).content == "fixed"
        assert await scope[SystemPromptStore].render() == "fixed"
        await scope.dispatch(Ping())
        assert seen == ["fixed"]


async def test_agent_registration_facade_uses_the_active_plugin_owner():
    seen = []

    class Feature(Plugin, needs=(Session,)):
        async def setup(self, scope, agent: Inject[Session]):
            agent.add_hook(Ping, lambda event: seen.append("explicit"))

            @agent.on(Ping)
            def listener(event):
                seen.append("decorated")

        @tool
        async def read(self) -> str:
            return "plugin tool"

    async with Application(
        model=ScriptedModel([]), plugins=[Approvals.allow_all()]
    ).session() as agent:
        handle = await agent.scope.plugin(Feature())
        assert [definition.name for definition in agent.scope[ToolRegistry].definitions] == ["read"]
        await agent.scope.dispatch(Ping())
        assert seen == ["explicit", "decorated"]
        await handle.aclose()
        assert agent.scope[ToolRegistry].definitions == ()
        await agent.scope.dispatch(Ping())
        assert seen == ["explicit", "decorated"]


async def test_registration_installation_rejects_closed_owners_and_tracks_early_disposal():
    async with Scope() as scope:
        states = []
        cleanups = []

        def install(state):
            states.append(state)
            return lambda: cleanups.append("removed")

        remove = scope.register(install)
        assert states[0].active
        await remove()
        assert not states[0].active
        await remove()
    assert cleanups == ["removed"]
    with pytest.raises(ScopeClosed):
        scope.register(install)
    assert len(states) == 1


async def test_entered_context_managers_exit_when_their_scope_closes():
    events = []

    @asynccontextmanager
    async def resource():
        events.append("enter")
        try:
            yield "value"
        finally:
            events.append("exit")

    async with Scope() as scope:
        assert await scope.enter(resource()) == "value"
        assert events == ["enter"]
    assert events == ["enter", "exit"]
    with pytest.raises(ScopeClosed):
        await scope.enter(resource())
