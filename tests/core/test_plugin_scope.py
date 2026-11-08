import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import override

import pytest

from harnessed import Plugin, PluginState, ToolCall, ToolResult, tool
from harnessed.core.tools import ToolRegistry
from harnessed.kernel import Emit, PluginScope, PluginStatus, Scope, as_plugin_spec, needs


class Ping(Emit):
    pass


@dataclass
class Connection:
    closed: bool = False


@pytest.mark.parametrize(
    "operation", ["plugin", "install", "settle", "extend", "aclose", "activate"]
)
async def test_plugin_receives_a_view_without_composition_or_lifetime_controls(operation):
    views = []

    class Feature(Plugin):
        @override
        async def setup(self, scope: PluginScope) -> None:
            views.append(scope)

        @override
        async def teardown(self, scope: PluginScope) -> None:
            views.append(scope)

    async with Scope() as root:
        await root.plugin(Feature())
        assert isinstance(views[0], PluginScope)
        assert not isinstance(views[0], Scope)
        assert not hasattr(views[0], operation)
        assert views[0].owner_name == "Feature"
    assert len(views) == 2 and views[0] is views[1]


async def test_setup_publishes_atomically_and_dependents_stop_before_resource_cleanup():
    started, release = asyncio.Event(), asyncio.Event()
    order, heard, registrations = [], [], []

    @asynccontextmanager
    async def connect() -> AsyncGenerator[Connection, None]:
        connection = Connection()
        try:
            yield connection
        finally:
            connection.closed = True
            order.append("connection.closed")

    class Provider(Plugin):
        values = PluginState(list[str])

        async def setup(self, scope: PluginScope) -> None:
            self.connection = await scope.enter(connect())
            scope.provide(Connection, self.connection)
            scope.on(Ping, lambda event: heard.append("ping"))

            def install(registration):
                registrations.append(registration)
                return lambda: order.append("registration.removed")

            scope.register(install)
            scope.effect(lambda: order.append(tuple(self.values)))
            assert scope[Connection] is self.connection
            started.set()
            async with asyncio.timeout(5):
                await release.wait()

        async def teardown(self, scope: PluginScope) -> None:
            assert not self.connection.closed
            self.values.append("provider.stopped")
            order.append("provider.stopped")

    class Consumer(Plugin):
        connection = needs(Connection)

        async def teardown(self, scope: PluginScope) -> None:
            assert not self.connection.closed
            order.append("consumer.stopped")

    async with asyncio.timeout(5), Scope() as root:
        consumer = await root.plugin(Consumer())
        starting = asyncio.create_task(root.plugin(Provider()))
        try:
            await started.wait()
            assert Connection not in root
            assert not registrations[0].active
            await root.dispatch(Ping())
            assert heard == []
            assert consumer.status is PluginStatus.WAITING
        finally:
            release.set()
        provider = await starting
        await root.settle()
        connection = root[Connection]
        assert consumer.status is PluginStatus.ACTIVE
        assert registrations[0].active
        await root.dispatch(Ping())
        assert heard == ["ping"]
        await provider.aclose()
        assert connection.closed
        assert Connection not in root
        assert not registrations[0].active
        await root.dispatch(Ping())
        assert heard == ["ping"]
    assert order == [
        "consumer.stopped",
        "provider.stopped",
        ("provider.stopped",),
        "registration.removed",
        "connection.closed",
    ]


@pytest.mark.parametrize("cancel", [False, True])
async def test_partial_setup_failure_or_cancellation_rolls_back_the_view(cancel):
    started, never = asyncio.Event(), asyncio.Event()
    cleaned, stopped, heard = [], [], []

    class Broken(Plugin):
        async def setup(self, scope: PluginScope) -> None:
            scope.effect(lambda: cleaned.append("first"))
            scope.provide(Connection, Connection())
            scope.on(Ping, lambda event: heard.append("leaked"))

            async def last() -> None:
                cleaned.append("last")

            scope.effect(last)
            started.set()
            if cancel:
                async with asyncio.timeout(5):
                    await never.wait()
            raise ValueError("setup failed")

        async def teardown(self, scope: PluginScope) -> None:
            stopped.append("unexpected teardown")

    async with asyncio.timeout(5), Scope() as root:
        starting = asyncio.create_task(root.plugin(Broken()))
        await started.wait()
        if cancel:
            starting.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else ValueError):
            await starting
        assert Connection not in root
        await root.dispatch(Ping())
        assert heard == []
    assert cleaned == ["last", "first"]
    assert stopped == []


async def test_dependency_replacement_creates_a_fresh_view_and_keeps_old_bindings_for_cleanup():
    views, cleaned = [], []

    class Feature(Plugin):
        backend = needs(str)

        async def setup(self, scope: PluginScope) -> None:
            views.append(scope)
            scope.effect(lambda: cleaned.append(self.backend))

    async with Scope() as root:
        root.provide(str, "first")
        handle = await root.plugin(Feature())
        remove = root.provide(str, "second")
        await root.settle()
        assert handle.status is PluginStatus.ACTIVE
        assert len(views) == 2 and views[0] is not views[1]
        assert cleaned == ["first"]
        await remove()
        await root.settle()
        assert len(views) == 3 and views[2] is not views[0]
        assert cleaned == ["first", "second"]
    assert cleaned == ["first", "second", "first"]


@pytest.mark.parametrize("phase", ["predicate", "tool"])
async def test_tool_callbacks_keep_ambient_registration_ownership_with_a_plugin_view(phase):
    class Marker(str):
        pass

    class Feature(Plugin):
        registry = needs(ToolRegistry)

        def install_converter(self) -> None:
            self.registry.result_converter(Marker, lambda value: ToolResult("converted"))

        def allowed(self) -> bool:
            if phase == "predicate":
                self.install_converter()
            return True

        @tool(readonly=True, enabled=allowed)
        def convert(self) -> str:
            if phase == "tool":
                self.install_converter()
            return "ready"

    recipe = Feature()
    async with Scope() as root:
        registry = ToolRegistry(root)
        root.provide(ToolRegistry, registry)
        registry.register(lambda: Marker("plain"), name="probe", owner=root)
        assert (await registry.execute(ToolCall("probe"))).text == "plain"
        handle = await root.plugin(recipe)
        assert (await registry.execute(ToolCall("convert"))).text == "ready"
        assert (await registry.execute(ToolCall("probe"))).text == "converted"
        await handle.aclose()
        assert (await registry.execute(ToolCall("probe"))).text == "plain"


def test_lifecycle_signature_requires_a_scope_parameter_before_activation():
    class MissingScope(Plugin):
        async def setup(self) -> None:
            raise AssertionError("must not run")

    with pytest.raises(TypeError, match="one scope: PluginScope parameter"):
        as_plugin_spec(MissingScope())


def test_generator_setup_is_rejected_without_starting_its_body():
    class GeneratorSetup(Plugin):
        async def setup(self, scope: PluginScope) -> AsyncGenerator[None, None]:
            raise AssertionError("must not run")
            yield

    with pytest.raises(TypeError, match="must be async and accept"):
        as_plugin_spec(GeneratorSetup())
