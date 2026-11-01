import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from harnessed import Plugin, PluginConfig
from harnessed.testing import mount_plugin


class ReadConfig(PluginConfig):
    limit: int = 100_000


class Reader(Plugin[ReadConfig]):
    pass


async def test_explicit_configuration_is_typed_validated_and_shared_across_activations():
    config = ReadConfig(limit=200)
    recipe = Reader(config=config)
    assert recipe.config is config
    async with mount_plugin(recipe) as one, mount_plugin(recipe) as two:
        assert one is not two and one is not recipe
        assert one.config is two.config is config
    with pytest.raises(ValidationError, match="frozen"):
        config.limit = 300


@pytest.mark.parametrize("config", [None, PluginConfig(), {}, "limit=100"])
def test_missing_or_wrong_configuration_fails_at_recipe_construction(config):
    with pytest.raises(TypeError, match="config"):
        Reader(config=config)


def test_untyped_configuration_keywords_and_legacy_class_alias_are_rejected():
    with pytest.raises(TypeError, match="limit"):
        Reader(limit=200)
    with pytest.raises(TypeError, match=r"Plugin\[YourConfig\]"):

        class Legacy(Plugin):
            Config = ReadConfig


async def test_configurationless_plugins_stay_plain_and_need_no_config_argument():
    class Observer(Plugin):
        pass

    observer = Observer()
    assert type(observer.config) is PluginConfig
    async with mount_plugin(observer) as active:
        assert active.config is observer.config


async def test_generic_intermediate_bases_and_inherited_specialization_keep_the_schema():
    class ExtendedConfig(ReadConfig):
        enabled: bool = True

    class GenericReader[ConfigT: ReadConfig](Plugin[ConfigT]):
        pass

    class Specialized(GenericReader[ExtendedConfig]):
        pass

    class Child(Specialized):
        pass

    config = ExtendedConfig(limit=50, enabled=False)
    async with mount_plugin(Child(config=config)) as active:
        assert active.config is config and not active.config.enabled
    with pytest.raises(TypeError, match="ExtendedConfig"):
        Child(config=ReadConfig())


def test_invalid_and_conflicting_generic_configuration_declarations_fail_early():
    class OtherConfig(PluginConfig):
        value: str = "other"

    class Other(Plugin[OtherConfig]):
        pass

    with pytest.raises(TypeError, match="incompatible"):

        class Conflicting(Reader, Other):
            pass

    with pytest.raises(TypeError, match="Pydantic configuration model"):

        class Invalid(Plugin[int]):
            pass


def test_external_models_must_be_frozen():
    class MutableConfig(BaseModel):
        value: int = 1

    class FrozenConfig(BaseModel):
        model_config = ConfigDict(frozen=True)
        value: int = 1

    class MutablePlugin(Plugin[MutableConfig]):
        pass

    class FrozenPlugin(Plugin[FrozenConfig]):
        pass

    with pytest.raises(TypeError, match="frozen"):
        MutablePlugin(config=MutableConfig())
    config = FrozenConfig()
    assert FrozenPlugin(config=config).config is config
