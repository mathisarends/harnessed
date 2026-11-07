import pytest

from harnessed.kernel import (
    Key,
    PluginBase,
    PluginStatus,
    Scope,
    as_plugin_spec,
    needs,
    observes,
)


class Feature(PluginBase, provides=True):
    backend = needs(str)
    optional = observes(int)

    async def setup(self, scope: Scope) -> None:
        assert scope[Feature] is self
        self.computed = self.backend.upper()


class Consumer(PluginBase, provides=True):
    feature = needs(Feature)


async def test_class_injection_precedes_setup_and_is_isolated_between_mounts():
    recipe = Feature()
    with pytest.raises(RuntimeError, match="mounted plugin"):
        _ = recipe.backend
    async with Scope() as first, Scope() as second:
        first.provide(str, "first")
        second.provide(str, "second")
        second.provide(int, 42)
        await first.install([Consumer(), recipe], strict=True)
        await second.plugin(recipe)
        mounted = first[Feature]
        assert first[Consumer].feature is mounted
        assert mounted.backend == "first"
        assert mounted.computed == "FIRST"
        assert mounted.optional is None
        assert second[Feature].backend == "second"
        assert second[Feature].optional == 42
    with pytest.raises(RuntimeError, match="mounted plugin"):
        _ = mounted.backend


async def test_explicit_class_dependency_waits_and_restarts():
    async with Scope() as scope:
        handle = await scope.plugin(Feature())
        assert handle.status is PluginStatus.WAITING
        remove = scope.provide(str, "one")
        await scope.settle()
        previous = scope[Feature]
        restore = scope.provide(str, "override")
        await scope.settle()
        assert scope[Feature] is not previous
        assert scope[Feature].backend == "override"
        await restore()
        await scope.settle()
        assert scope[Feature].backend == "one"
        await remove()
        await scope.settle()
        assert Feature not in scope
        scope.provide(str, "two")
        await scope.settle()
        assert scope[Feature] is not previous
        assert scope[Feature].backend == "two"


async def test_inherited_provides_uses_declaring_class_and_named_keys():
    class Specialized(Feature):
        pass

    key = Key[PluginBase]("feature")

    class Named(PluginBase, provides=key):
        pass

    assert as_plugin_spec(Specialized()).provides == (Feature,)
    async with Scope() as scope:
        scope.provide(str, "ready")
        await scope.install([Specialized(), Named()], strict=True)
        assert type(scope[Feature]) is Specialized
        assert Specialized not in scope
        assert type(scope[key]) is Named


async def test_field_requirement_is_validated_before_mount():
    class Deferred(PluginBase):
        backend = needs(str)

    recipe = Deferred()
    with pytest.raises(RuntimeError, match="mounted plugin"):
        _ = recipe.backend
    async with Scope() as scope:
        with pytest.raises(RuntimeError, match="Unresolved plugin dependencies"):
            await scope.install([recipe], strict=True)


async def test_private_dependency_has_the_same_recipe_guard_and_mount_binding():
    class Private(PluginBase, provides=True):
        _backend = needs(str)

    recipe = Private()
    with pytest.raises(RuntimeError, match="mounted plugin"):
        _ = recipe._backend
    async with Scope() as scope:
        scope.provide(str, "ready")
        await scope.plugin(recipe)
        assert scope[Private]._backend == "ready"
        assert as_plugin_spec(recipe).needs == (str,)


async def test_failed_setup_removes_automatic_provision():
    class Broken(PluginBase, provides=True):
        async def setup(self) -> None:
            raise ValueError("broken")

    async with Scope() as scope:
        with pytest.raises(ValueError, match="broken"):
            await scope.plugin(Broken())
        assert Broken not in scope


async def test_strict_install_detects_declared_cycles_before_setup():
    entered = []
    first_key = Key[PluginBase]("first")
    second_key = Key[PluginBase]("second")

    class First(PluginBase, provides=first_key, needs=(second_key,)):
        async def setup(self) -> None:
            entered.append(self)

    class Second(PluginBase, provides=second_key, needs=(first_key,)):
        pass

    async with Scope() as scope:
        with pytest.raises(RuntimeError, match="cyclic"):
            await scope.install([First(), Second()], strict=True)
        assert entered == []
