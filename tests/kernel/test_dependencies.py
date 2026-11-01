import asyncio
from typing import Protocol

import pytest

from harnessed.kernel import (
    Context,
    Emit,
    Inject,
    Key,
    PluginBase,
    PluginState,
    PluginStatus,
    as_plugin_spec,
    dependency,
    hook,
)


class Backend(Protocol):
    def read(self) -> str: ...


class FakeBackend:
    def __init__(self, text: str) -> None:
        self.text = text

    def read(self) -> str:
        return self.text


async def test_fields_wait_without_needs_and_preserve_binding_during_every_cleanup():
    instances, cleaned = [], []

    class Feature(PluginBase):
        backend = dependency(Backend)
        state = PluginState(list[str])

        async def setup(self, ctx: Context) -> None:
            instances.append(self)
            self.state.append(self.backend.read())
            ctx.effect(lambda: cleaned.append((self.backend.read(), tuple(self.state))))

        async def teardown(self) -> None:
            self.state.append("teardown")

    recipe = Feature()
    with pytest.raises(RuntimeError, match="recipe"):
        _ = recipe.backend
    async with Context() as ctx:
        handle = await ctx.plugin(recipe)
        assert handle.status is PluginStatus.WAITING
        assert handle.missing_services == (Backend,)
        assert ctx.plugin_diagnostics()[0].missing == (Backend,)
        ctx.provide(Backend, FakeBackend("one"))
        await ctx.settle()
        first = handle.instance
        assert first is instances[0] and first is not recipe
        restore = ctx.provide(Backend, FakeBackend("two"))
        await ctx.settle()
        assert instances[1] is not first
        assert instances[1].state == ["two"]
        assert cleaned == [("one", ("one", "teardown"))]
        with pytest.raises(RuntimeError, match="disposed activation"):
            _ = instances[0].backend
        await restore()
        await ctx.settle()
        assert instances[2].backend.read() == "one"
        assert instances[2].state == ["one"]
    assert cleaned[-1] == ("one", ("one", "teardown"))
    assert handle.instance is None


async def test_optional_field_restarts_on_appearance_replacement_and_disappearance():
    starts, cleaned = [], []

    class Feature(PluginBase):
        backend = dependency(str, optional=True)
        values = PluginState(list[str])

        async def setup(self, ctx: Context) -> None:
            starts.append(self)
            ctx.effect(lambda: cleaned.append(self.backend))

    recipe = Feature()
    spec = as_plugin_spec(recipe)
    assert spec.needs == () and spec.observes == (str,)
    async with Context() as ctx:
        handle = await ctx.plugin(recipe)
        assert handle.status is PluginStatus.ACTIVE and starts[0].backend is None
        assert ctx.plugin_diagnostics() == ()
        remove = ctx.provide(str, "first")
        await ctx.settle()
        assert starts[1].backend == "first" and starts[1].values == []
        override = ctx.provide(str, "second")
        await ctx.settle()
        assert starts[2].backend == "second"
        await override()
        await ctx.settle()
        assert starts[3].backend == "first"
        await remove()
        await ctx.settle()
        assert starts[4].backend is None
    assert cleaned == [None, "first", "second", "first", None]
    assert len({id(instance) for instance in starts}) == 5


async def test_named_fields_inheritance_overrides_and_normalized_requirements():
    key = Key[str]("workspace.backend")

    class Parent(PluginBase, needs=(key, key)):
        backend = dependency(str)
        optional = dependency(key, optional=True)

    class Child(Parent):
        backend = dependency(key)
        annotation_only: int

    recipe = Child(needs=(key, int, key))
    spec = as_plugin_spec(recipe)
    assert spec.needs == (key, int) and spec.observes == ()
    async with Context() as root:
        root.provide(key, "parent")
        root.provide(int, 1)
        child = root.extend()
        child.provide(key, "child")
        first = await root.plugin(recipe)
        second = await child.plugin(recipe)
        assert isinstance(first.instance, Child) and isinstance(second.instance, Child)
        assert first.instance.backend == "parent"
        assert second.instance.backend == second.instance.optional == "child"
        with pytest.raises(AttributeError, match="read-only"):
            second.instance.backend = "changed"
        assert not hasattr(second.instance, "annotation_only")


async def test_constructor_values_are_fresh_and_explicit_clients_keep_identity():
    client = asyncio.Lock()

    class Feature(PluginBase):
        def __init__(self, client: asyncio.Lock, *, label: str = "default") -> None:
            super().__init__()
            self.client = client
            self.label = label
            self.values: list[str] = []

    recipe = Feature(client, label="workspace")
    async with Context() as one, Context() as two:
        first = (await one.plugin(recipe)).instance
        second = (await two.plugin(recipe)).instance
        assert isinstance(first, Feature) and isinstance(second, Feature)
        first.values.append("one")
        assert second.values == recipe.values == []
        assert first.values is not second.values
        assert first.client is second.client is client
        assert first.label == second.label == "workspace"
        assert first.recipe is second.recipe is recipe


def test_class_inject_fields_are_rejected_with_migration_hint():
    with pytest.raises(TypeError, match=r"use dependency\(key\)"):

        class Old(PluginBase):
            backend: Inject[str]


async def test_event_observers_need_no_producer():
    class Notice(Emit):
        pass

    seen = []

    class Observer(PluginBase):
        @hook(Notice)
        def observe(self, event: Notice) -> None:
            seen.append(event)

    async with Context() as ctx:
        handle = await ctx.plugin(Observer())
        assert handle.status is PluginStatus.ACTIVE and handle.spec.needs == ()
        event = Notice()
        await ctx.dispatch(event)
        assert seen == [event]
