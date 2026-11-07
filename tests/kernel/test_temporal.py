import asyncio
from dataclasses import dataclass

import pytest

from harnessed import Plugin, ToolCall, plugin
from harnessed.core.plugin import Bundle
from harnessed.core.prompt.system_prompt import SystemPromptStore
from harnessed.core.runtime.services import RuntimeServices
from harnessed.core.tools.decorator import tool
from harnessed.core.tools.registry import ToolRegistry
from harnessed.kernel import Emit, MissingService, PluginStatus, Scope, needs


@dataclass
class Backend:
    name: str


async def test_class_keyword_dependencies_suspend_and_restart_the_plugin():
    seen = []

    class Feature(Plugin, needs=(Backend,)):
        backend = needs(Backend)

        async def setup(self) -> None:
            seen.append(self.backend.name)

    async with Scope() as scope:
        handle = await scope.plugin(Feature())
        assert handle.status == PluginStatus.WAITING
        remove = scope.provide(Backend, Backend("first"))
        await scope.settle()
        assert seen == ["first"]
        await remove()
        await scope.settle()
        assert handle.status == PluginStatus.WAITING
        scope.provide(Backend, Backend("second"))
        await scope.settle()
        assert seen == ["first", "second"]


class Feature:
    pass


class Ping(Emit):
    pass


async def test_explicit_needs_waits_and_rebuilds_with_fresh_scope_and_local_state():
    lifecycle, seen = [], []

    @plugin(needs=[Backend])
    async def consumer(scope):
        backend = scope[Backend]
        calls = 0
        lifecycle.append(f"start:{backend.name}")

        @scope.on(Ping)
        def observe(event):
            nonlocal calls
            calls += 1
            seen.append((backend.name, calls))

        yield
        lifecycle.append(f"stop:{backend.name}")

    async with Scope() as scope:
        handle = await scope.plugin(consumer())
        assert handle.status == PluginStatus.WAITING
        remove = scope.provide(Backend, Backend("one"))
        await scope.settle()
        first_scope = handle.scope
        assert handle.status == PluginStatus.ACTIVE
        await scope.dispatch(Ping())
        await remove()
        await scope.settle()
        assert handle.status == PluginStatus.WAITING and first_scope._closed
        await scope.dispatch(Ping())
        scope.provide(Backend, Backend("two"))
        await scope.settle()
        assert handle.scope is not first_scope
        await scope.dispatch(Ping())
        assert seen == [("one", 1), ("two", 1)]
        assert lifecycle == ["start:one", "stop:one", "start:two"]
    assert lifecycle[-1] == "stop:two"


async def test_provider_unload_suspends_consumers_and_another_provider_reactivates_them():
    observed = []

    @plugin
    async def provider(scope, name):
        scope.provide(Backend, Backend(name))
        yield

    @plugin(needs=[Backend])
    async def consumer(scope):
        observed.append(scope[Backend].name)
        yield

    async with Scope() as scope:
        old = await scope.plugin(provider("one"))
        dependent = await scope.plugin(consumer())
        await old.aclose()
        assert old.status == PluginStatus.DISPOSED and dependent.status == PluginStatus.WAITING
        await scope.plugin(provider("two"))
        await scope.settle()
        assert dependent.status == PluginStatus.ACTIVE and observed == ["one", "two"]


async def test_transitive_needs_teardown_and_rebuild_in_dependency_order():
    lifecycle = []

    @plugin(needs=[Backend])
    async def middle(scope):
        scope.provide(Feature, Feature())
        lifecycle.append("middle+")
        yield
        lifecycle.append("middle-")

    @plugin(needs=[Feature])
    async def leaf(scope):
        lifecycle.append("leaf+")
        yield
        lifecycle.append("leaf-")

    async with Scope() as scope:
        handles = await scope.install([leaf(), middle()])
        assert all(handle.status == PluginStatus.WAITING for handle in handles)
        remove = scope.provide(Backend, Backend("one"))
        await scope.settle()
        assert lifecycle == ["middle+", "leaf+"]
        await remove()
        await scope.settle()
        assert lifecycle[-2:] == ["leaf-", "middle-"]
        assert Feature not in scope
        scope.provide(Backend, Backend("two"))
        await scope.settle()
        assert lifecycle[-2:] == ["middle+", "leaf+"]


async def test_service_shadow_and_fallback_each_recompose_the_consumer():
    seen = []

    @plugin(needs=[Backend])
    async def consumer(scope):
        seen.append(scope[Backend].name)
        yield

    async with Scope() as scope:
        scope.provide(Backend, Backend("base"))
        await scope.plugin(consumer())
        override = scope.provide(Backend, Backend("override"))
        await scope.settle()
        await override()
        await scope.settle()
        assert seen == ["base", "override", "base"]


async def test_scope_isolation_and_permanent_disposal_of_waiting_plugin():
    seen = []

    @plugin(needs=[Backend])
    async def consumer(scope, label):
        seen.append((label, scope[Backend].name))
        yield

    async with Scope() as root:
        left, right = root.extend(), root.extend()
        left.provide(Backend, Backend("local"))
        one = await left.plugin(consumer("left"))
        two = await right.plugin(consumer("right"))
        root_remove = root.provide(Backend, Backend("root"))
        await root.settle()
        assert seen == [("left", "local"), ("right", "root")]
        await root_remove()
        await root.settle()
        assert one.status == PluginStatus.ACTIVE and two.status == PluginStatus.WAITING
        await two.aclose()
        root.provide(Backend, Backend("new root"))
        await root.settle()
        assert two.status == PluginStatus.DISPOSED and len(seen) == 2


async def test_class_and_bundle_explicit_needs_remove_tools_and_prompt_sections():
    class FeaturePlugin(Plugin, needs=(Backend, SystemPromptStore)):
        prompt = needs(SystemPromptStore)

        async def setup(self, scope):
            self.name = scope[Backend].name
            self.calls = 0
            self.prompt.fixed(self.name)

        @tool
        async def read(self) -> str:
            self.calls += 1
            return f"{self.name}:{self.calls}"

    bundle = Bundle("observer", needs=[Backend])
    events = []
    bundle.on(Ping, lambda event: events.append("ping"))

    async with Scope() as scope:
        await scope.install([RuntimeServices(), FeaturePlugin(), bundle])
        remove = scope.provide(Backend, Backend("first"))
        await scope.settle()
        assert await scope[SystemPromptStore].render() == "first"
        assert (await scope[ToolRegistry].execute(ToolCall("read"))).content == "first:1"
        await scope.dispatch(Ping())
        await remove()
        await scope.settle()
        assert scope[ToolRegistry].definitions == ()
        assert await scope[SystemPromptStore].render() == ""
        await scope.dispatch(Ping())
        scope.provide(Backend, Backend("second"))
        await scope.settle()
        assert (await scope[ToolRegistry].execute(ToolCall("read"))).content == "second:1"
        assert events == ["ping"]


async def test_background_setup_failure_rolls_back_and_reports_once_then_recovers():
    @plugin(needs=[Backend])
    async def conditional(scope):
        scope.provide(Feature, Feature())
        if scope[Backend].name == "broken":
            raise ValueError("activation failed")
        yield

    async with Scope() as scope:
        handle = await scope.plugin(conditional())
        remove = scope.provide(Backend, Backend("broken"))
        with pytest.raises(ExceptionGroup, match="composition failed"):
            await scope.settle()
        assert handle.status == PluginStatus.FAILED and Feature not in scope
        await scope.settle()
        await remove()
        scope.provide(Backend, Backend("fixed"))
        await scope.settle()
        assert handle.status == PluginStatus.ACTIVE and Feature in scope and handle.error is None


async def test_services_are_not_published_to_consumers_until_provider_setup_yields():
    started, ready = asyncio.Event(), asyncio.Event()
    seen = []

    @plugin
    async def provider(scope):
        scope.provide(Backend, Backend("ready"))
        assert scope[Backend].name == "ready"
        started.set()
        await ready.wait()
        yield

    @plugin(needs=[Backend])
    async def consumer(scope):
        seen.append(scope[Backend].name)
        yield

    async with Scope() as scope:
        handle = await scope.plugin(consumer())
        mounting = asyncio.create_task(scope.plugin(provider()))
        await asyncio.wait_for(started.wait(), timeout=5)
        await scope.settle()
        assert Backend not in scope and handle.status == PluginStatus.WAITING and seen == []
        with pytest.raises(MissingService):
            scope[Backend]
        ready.set()
        await asyncio.wait_for(mounting, timeout=5)
        await scope.settle()
        assert seen == ["ready"]


async def test_missing_multiple_needs_stay_dormant_without_busy_retry():
    @plugin(needs=[Backend, Feature])
    async def both(scope):
        yield

    async with Scope() as scope:
        handle = await scope.plugin(both())
        scope.provide(Backend, Backend("one"))
        await asyncio.wait_for(scope.settle(), 1)
        assert handle.status == PluginStatus.WAITING
        scope.provide(Feature, Feature())
        await scope.settle()
        assert handle.status == PluginStatus.ACTIVE


async def test_recomposition_runs_automatically_without_an_explicit_settle():
    started = asyncio.Event()

    @plugin(needs=[Backend])
    async def consumer(scope):
        started.set()
        yield

    async with Scope() as scope:
        handle = await scope.plugin(consumer())
        scope.provide(Backend, Backend("one"))
        await asyncio.wait_for(started.wait(), 1)
        assert handle.status == PluginStatus.ACTIVE


async def test_dependency_loss_cancels_unfinished_setup_and_retries_when_service_returns():
    started, cleaned = asyncio.Event(), []

    @plugin(needs=[Backend])
    async def consumer(scope):
        scope.effect(lambda: cleaned.append("cleanup"))
        if scope[Backend].name == "slow":
            started.set()
            await asyncio.Event().wait()
        yield

    async with Scope() as scope:
        handle = await scope.plugin(consumer())
        remove = scope.provide(Backend, Backend("slow"))
        await asyncio.wait_for(started.wait(), timeout=5)
        await remove()
        await asyncio.wait_for(scope.settle(), 1)
        assert handle.status == PluginStatus.WAITING and cleaned == ["cleanup"]
        scope.provide(Backend, Backend("fast"))
        await scope.settle()
        assert handle.status == PluginStatus.ACTIVE


async def test_context_close_cancels_background_setup():
    started, cleaned = asyncio.Event(), []

    @plugin(needs=[Backend])
    async def consumer(scope):
        scope.effect(lambda: cleaned.append("cleanup"))
        started.set()
        await asyncio.Event().wait()
        yield

    scope = Scope()
    handle = await scope.plugin(consumer())
    scope.provide(Backend, Backend("one"))
    await asyncio.wait_for(started.wait(), timeout=5)
    await asyncio.wait_for(scope.aclose(), 1)
    assert handle.status == PluginStatus.DISPOSED and cleaned == ["cleanup"]


async def test_wrapping_a_needed_service_keeps_the_upstream_dependency():
    @plugin(needs=[Backend])
    async def wrapper(scope):
        upstream = scope[Backend]
        scope.provide(Backend, Backend(f"wrapped {upstream.name}"))
        yield

    async with Scope() as scope:
        remove = scope.provide(Backend, Backend("one"))
        handle = await scope.plugin(wrapper())
        await scope.settle()
        assert scope[Backend].name == "wrapped one"
        await remove()
        await scope.settle()
        assert Backend not in scope and handle.status == PluginStatus.WAITING
        scope.provide(Backend, Backend("two"))
        await scope.settle()
        assert scope[Backend].name == "wrapped two"


async def test_dynamic_dependency_cycle_reports_without_busy_loop_and_can_recover():
    @plugin(needs=[Backend])
    async def first(scope):
        scope.provide(Feature, Feature())
        yield

    @plugin(needs=[Feature])
    async def second(scope):
        scope.provide(Backend, Backend("cyclic"))
        yield

    async with Scope() as scope:
        scope.provide(Backend, Backend("base"))
        one = await scope.plugin(first())
        two = await scope.plugin(second())
        with pytest.raises(ExceptionGroup) as error:
            await asyncio.wait_for(scope.settle(), 1)
        assert "Cyclic plugin needs" in str(error.value.exceptions[0])
        await two.aclose()
        await scope.settle()
        assert one.status == PluginStatus.ACTIVE and scope[Backend].name == "base"
