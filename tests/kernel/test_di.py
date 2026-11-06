from typing import Annotated

import pytest

from harnessed.kernel import (
    Bundle,
    Emit,
    Inject,
    InjectMarker,
    Key,
    MissingService,
    PluginBase,
    PluginStatus,
    Scope,
    as_plugin_spec,
    binding_plan,
    hook,
    plugin,
)


async def test_injection_binds_values_without_creating_lifetime_dependencies():
    class Changed(Emit):
        pass

    seen = []

    class Feature(PluginBase, provides=True):
        async def setup(self, value: Inject[str]) -> None:
            self.initial = value

        @hook(Changed)
        def observe(self, event: Changed, value: Inject[str]) -> None:
            seen.append(value)

    async with Scope() as scope:
        scope.provide(str, "first")
        handle = await scope.plugin(Feature())
        assert handle.spec.needs == ()
        mounted = scope[Feature]
        assert mounted.initial == "first"
        await scope.dispatch(Changed())
        restore = scope.provide(str, "second")
        await scope.settle()
        assert scope[Feature] is mounted
        assert mounted.initial == "first"
        await scope.dispatch(Changed())
        await restore()
        await scope.settle()
        await scope.dispatch(Changed())
        assert seen == ["first", "second", "first"]


async def test_missing_required_setup_injection_without_needs_fails_at_resolution():
    class Setup(PluginBase):
        async def setup(self, value: Inject[str]) -> None:
            pass

    async with Scope() as scope:
        with pytest.raises(MissingService, match="str"):
            await scope.plugin(Setup())


def test_bundle_configured_hooks_and_function_injections_do_not_add_needs():
    class Changed(Emit):
        pass

    def observe(event: Changed, value: Inject[str]) -> None:
        pass

    bundle = Bundle("observer")
    bundle.on(Changed, observe)
    configured = PluginBase(hooks=((Changed, observe),))

    @plugin
    async def consumer(scope: Scope, value: Inject[str]):
        yield

    for recipe in (bundle, configured, consumer()):
        assert as_plugin_spec(recipe).needs == ()


async def test_listener_resolves_current_service_without_repeating_reflection(monkeypatch):
    import harnessed.kernel.di as di

    class Changed(Emit):
        pass

    seen = []

    def listener(event, value: Inject[str]):
        seen.append(value)

    async with Scope() as scope:
        scope.provide(str, "first")
        scope.on(Changed, listener)

        def unexpected(*args, **kwargs):
            raise AssertionError("reflection repeated after registration")

        monkeypatch.setattr(di, "get_type_hints", unexpected)
        await scope.dispatch(Changed())
        scope.provide(str, "second")
        await scope.dispatch(Changed())
    assert seen == ["first", "second"]


async def test_optional_injection_does_not_create_a_hard_lifetime_dependency():
    starts, values = [], []

    @plugin
    async def optional(scope: Scope, value: Inject[str | None]):
        starts.append(value)
        yield

    def read(value: Inject[str | None]):
        values.append(value)

    async with Scope() as scope:
        handle = await scope.plugin(optional())
        assert handle.status == PluginStatus.ACTIVE
        binding_plan(read).bind(scope)
        remove = scope.provide(str, "available")
        await scope.settle()
        bound = binding_plan(read).bind(scope)
        read(*bound.args, **bound.kwargs)
        await remove()
        await scope.settle()
        bound = binding_plan(read).bind(scope)
        read(*bound.args, **bound.kwargs)
        assert starts == [None]
        assert handle.status == PluginStatus.ACTIVE
    assert values == ["available", None]


async def test_named_key_injection_uses_the_same_key_as_needs():
    selected = Key[str]("selected")

    def read(value: Annotated[str, InjectMarker(selected)]):
        return value

    async with Scope() as scope:
        scope.provide(str, "class service")
        scope.provide(selected, "named service")
        bound = binding_plan(read).bind(scope)
        assert read(*bound.args, **bound.kwargs) == "named service"
        with pytest.raises(TypeError, match="cannot be supplied"):
            binding_plan(read).bind(scope, kwargs={"value": "spoofed"})


def test_plugin_config_keywords_are_immutable_and_detached():
    @plugin
    async def configured(scope, *, value: int):
        yield

    spec = configured(value=1)
    with pytest.raises(TypeError):
        spec.kwargs["value"] = 2
