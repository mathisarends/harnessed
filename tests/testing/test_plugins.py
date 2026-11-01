import pytest
from pydantic import ValidationError

from harnessed import Instructions, Plugin, PluginConfig, ToolCall, tool
from harnessed.core.tools import ToolRegistry
from harnessed.kernel import Context, PluginState, dependency
from harnessed.plugins import Service
from harnessed.testing import mount_plugin


class FeatureConfig(PluginConfig):
    prefix: str = "result"


class Feature(Plugin[FeatureConfig]):
    backend = dependency(str)
    values = PluginState(list[str])
    usage = Instructions(text="Explicit placement is required only in an agent.")

    async def setup(self, ctx: Context) -> None:
        ctx.effect(lambda: self.values.append("closed"))

    @tool(readonly=True)
    async def read(self) -> str:
        return f"{self.config.prefix}: {self.backend}"

    @tool(readonly=True)
    def read_sync(self) -> str:
        return self.backend


async def test_mount_helper_exposes_typed_instance_and_guarantees_cleanup_on_error():
    recipe = Feature(config=FeatureConfig(prefix="fake"))
    with pytest.raises(RuntimeError, match="active plugin instance"):
        await recipe.read()
    with pytest.raises(RuntimeError, match="active plugin instance"):
        recipe.read_sync()
    with pytest.raises(ValueError, match="body"):
        async with mount_plugin(recipe, services=(Service(str, "backend"),)) as active:
            values = active.values
            assert active is not recipe and active.config.prefix == "fake"
            assert active.config is recipe.config
            assert await active.read() == "fake: backend"
            assert active.read_sync() == "backend"
            raise ValueError("body")
    assert values == ["closed"]
    with pytest.raises(RuntimeError, match="disposed activation"):
        await active.read()
    with pytest.raises(RuntimeError, match="mounted plugin"):
        _ = active.values


def test_recipe_config_is_frozen_and_cannot_be_replaced():
    recipe = Feature(config=FeatureConfig())
    with pytest.raises(ValidationError, match="frozen"):
        recipe.config.prefix = "changed"
    with pytest.raises(AttributeError, match="cannot be replaced"):
        recipe.config = FeatureConfig(prefix="changed")


async def test_missing_field_has_waiting_diagnostic_without_a_model():
    with pytest.raises(RuntimeError, match="Unresolved plugin dependencies.*Feature.*str"):
        async with mount_plugin(Feature(config=FeatureConfig())):
            pytest.fail("Missing dependencies must not expose an instance")


async def test_keyword_self_does_not_bypass_recipe_guard():
    with pytest.raises(RuntimeError, match="active plugin instance"):
        await Feature.read(self=Feature(config=FeatureConfig()))


async def test_method_guard_does_not_depend_on_the_instance_parameter_name():
    class Unusual(Plugin):
        @tool(readonly=True)
        async def read(owner) -> str:
            return "active"

    recipe = Unusual()
    with pytest.raises(RuntimeError, match="active plugin instance"):
        await recipe.read()
    with pytest.raises(RuntimeError, match="active plugin instance"):
        await Unusual.read(owner=recipe)
    async with mount_plugin(recipe) as active:
        assert await active.read() == "active"


async def test_custom_constructor_must_initialize_plugin_configuration():
    class Broken(Plugin):
        def __init__(self) -> None:
            self.value = "unconfigured"

    with pytest.raises(TypeError, match=r"must call super\(\).__init__"):
        async with mount_plugin(Broken()):
            pytest.fail("A constructor skipping Plugin initialization must fail clearly")


async def test_prepared_call_keeps_original_activation_after_dependency_replacement():
    executed = []

    class Reader(Plugin):
        backend = dependency(str)

        @tool(readonly=True)
        async def read(self) -> str:
            executed.append(self.backend)
            return self.backend

    async with Context() as ctx:
        registry = ToolRegistry(ctx)
        ctx.provide(ToolRegistry, registry)
        ctx.provide(str, "first")
        handle = await ctx.plugin(Reader())
        original = handle.instance
        plan = registry.plan(ToolCall("read"))
        assert registry.check_enabled(plan, ctx=ctx) is None
        ctx.provide(str, "replacement")
        await ctx.settle()
        assert handle.instance is not original
        rejected = registry.check_enabled(plan, ctx=ctx)
        assert rejected is not None and rejected.is_error
        assert "registration changed" in rejected.text
        assert executed == []
        assert (await registry.execute(ToolCall("read"))).text == "replacement"
        assert executed == ["replacement"]
