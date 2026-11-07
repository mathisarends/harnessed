from dataclasses import FrozenInstanceError

import pytest

from harnessed import (
    Agent,
    CompositionDescription,
    Instructions,
    Plugin,
    PluginPack,
    SystemPrompt,
)
from harnessed.core.composition import compose_plugins
from harnessed.errors import PluginConflict, PluginDependencyError
from harnessed.kernel import Bundle, PluginLike, Scope, plugin
from harnessed.plugins import AgentLoop, Compaction
from harnessed.testing import ScriptedModel


class Choice(Plugin, slot="example.choice"):
    pass


class Extra(Plugin):
    pass


@pytest.mark.parametrize("explicit_first", [False, True])
def test_direct_override_keeps_the_inherited_position_regardless_of_input_order(explicit_first):
    before, after = Extra(name="before"), Extra(name="after")
    original, replacement = Choice(name="original"), Choice(name="replacement")
    pack = PluginPack("library", [before, original, after])
    inputs = [replacement, pack] if explicit_first else [pack, replacement]
    resolved = compose_plugins(inputs)
    assert resolved.plugins == (before, replacement, after)
    description = resolved.description.plugins[1]
    assert description.origin == "explicit / replacement"
    assert description.replaces[0].origin == "library / original"


def test_local_overrides_compose_at_each_boundary_and_retain_history():
    base, library, application = (Choice(name=name) for name in ("base", "library", "application"))
    pack = PluginPack("outer", [PluginPack("inner", [base]), library])
    resolved = compose_plugins([pack, application])
    assert resolved.plugins == (application,)
    previous = resolved.description.plugins[0].replaces[0]
    assert previous.origin == "outer / library"
    assert previous.replaces[0].origin == "outer / inner / base"


@pytest.mark.parametrize("wrapped", [False, True])
def test_sibling_pack_conflicts_do_not_depend_on_internal_depth(wrapped):
    first = PluginPack("first", [Choice(name="one")])
    second = PluginPack("second", [Choice(name="two")])
    if wrapped:
        first = PluginPack("wrapper", [first])
    with pytest.raises(PluginConflict, match="example.choice") as failure:
        compose_plugins([first, second])
    assert "first / one" in str(failure.value)
    assert "second / two" in str(failure.value)
    explicit = Choice(name="explicit")
    resolved = compose_plugins([first, explicit, second])
    assert resolved.plugins == (explicit,)
    assert len(resolved.description.plugins[0].replaces) == 2


@pytest.mark.parametrize("nested", [False, True])
def test_duplicate_direct_slots_are_errors_even_if_the_parent_supplies_an_override(nested):
    inputs = [Choice(name="one"), Choice(name="two")]
    if nested:
        inputs = [PluginPack("invalid", inputs), Choice(name="parent")]
    with pytest.raises(PluginConflict, match="one, .*two"):
        compose_plugins(inputs)


def test_unslotted_plugins_are_additive_even_when_they_provide_the_same_service():
    class Provider(Plugin, provides=True):
        pass

    first, second = Provider(), Provider()
    resolved = compose_plugins([PluginPack("library", [first]), second])
    assert resolved.plugins == (first, second)
    assert resolved.description.plugins[0].provides == (Provider,)


def test_library_defaults_override_agent_fallbacks_and_explicit_inputs_override_both():
    library = AgentLoop(max_steps=12)
    app = Agent(plugins=[PluginPack("library", [PluginPack("nested", [library])])])
    assert app.plugins[1] is library
    row = next(item for item in app.describe().plugins if item.slot == "loop")
    assert row.pack_path == ("library", "nested")
    assert row.replaces[0].pack_path == ("harnessed",)
    explicit = AgentLoop(max_steps=7)
    app = Agent(plugins=[PluginPack("library", [library]), explicit])
    assert app.plugins[1] is explicit


def test_without_is_recursive_matches_subclasses_and_preserves_the_original():
    class Derived(Extra):
        pass

    first, second = Extra(name="first"), Derived(name="second")
    child = PluginPack("child", [second, Choice()])
    base = PluginPack("base", [first, child])
    filtered = base.without(Extra)
    assert base.plugins == (first, child)
    assert child.plugins[0] is second
    resolved = compose_plugins([PluginPack("application", [filtered])])
    assert len(resolved.plugins) == 1
    assert [item.plugin.origin for item in resolved.removed] == [
        "application / base / first",
        "application / base / child / second",
    ]
    assert all(item.removed_by == ("application", "base") for item in resolved.removed)


def test_chained_without_and_reused_child_packs_keep_the_excluding_scope():
    child = PluginPack("child", [Extra(name="child-extra"), Choice()]).without(Extra)
    base = PluginPack("base", [child, Compaction()]).without(Compaction)
    filtered = base.without(Choice)
    resolved = compose_plugins([PluginPack("application", [filtered])])
    removed = {item.plugin.name: item for item in resolved.removed}
    assert removed["child-extra"].removed_by == ("application", "base", "child")
    assert removed["Choice"].plugin.pack_path == ("application", "base", "child")
    assert removed["Choice"].removed_by == ("application", "base")
    assert removed["Compaction"].removed_by == ("application", "base")
    other = compose_plugins([PluginPack("other", [child])])
    assert other.removed[0].removed_by == ("other", "child")


def test_without_requires_every_type_to_match_and_rejects_non_types():
    base = PluginPack("library", [Extra()])
    with pytest.raises(ValueError, match="Choice"):
        base.without(Extra, Choice)
    assert len(base.plugins) == 1
    with pytest.raises(ValueError, match="Extra"):
        base.without(Extra).without(Extra)
    with pytest.raises(TypeError, match="recipe types"):
        base.without(Extra())


def test_pack_captures_iterables_and_keeps_its_structure_immutable():
    recipes = [Extra()]
    pack = PluginPack("library", iter(recipes))
    recipes.clear()
    assert len(pack.plugins) == 1
    with pytest.raises(FrozenInstanceError):
        pack.name = "changed"
    with pytest.raises(ValueError, match="empty"):
        PluginPack("  ", [])
    with pytest.raises(TypeError, match="string"):
        PluginPack(42, [])


@pytest.mark.parametrize("invalid", [False, 42, [Extra()]])
def test_composition_inputs_require_plugins_or_explicit_packs(invalid):
    with pytest.raises(TypeError, match="Expected a configured plugin"):
        Agent(plugins=[PluginPack("library", [invalid])])


def test_function_factories_are_configured_once_and_kernel_bundles_remain_recipes():
    @plugin(slot="example.function")
    async def factory(scope: Scope):
        yield

    bundle = Bundle("hooks")
    configured = factory()
    inputs: list[PluginLike] = [bundle, factory, configured]
    with pytest.raises(PluginConflict, match="example.function"):
        compose_plugins(inputs)
    resolved = compose_plugins([PluginPack("library", [factory, bundle]), configured])
    assert resolved.plugins == (configured, bundle)


def test_callable_plugin_instances_keep_their_prompt_metadata():
    class CallablePlugin(Plugin):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt(Instructions(text="Keep this contribution."))

        def __call__(self):
            pass

    recipe = CallablePlugin()
    agent = Agent(
        plugins=[PluginPack("library", [recipe])],
    )
    assert recipe in agent.plugins
    assert "Keep this contribution." in str(agent.prompt)


def test_description_is_static_structured_and_immutable():
    class Unmounted(Plugin, provides=True):
        async def setup(self) -> None:
            pytest.fail("describe() must not mount plugins")

    pack = PluginPack("library", [Choice(name="original"), Extra(), Unmounted()]).without(Extra)
    agent = Agent(plugins=[pack, Choice(name="replacement")])
    description = agent.describe()
    assert isinstance(description, CompositionDescription)
    assert description is agent.describe()
    row = next(item for item in description.plugins if item.slot == "example.choice")
    assert row.replaces[0].origin == "library / original"
    assert description.removed[0].removed_by == ("library",)
    assert "provides: Unmounted" in str(description)
    assert "replaces: library / original" in str(description)
    assert "removed: library / Extra by library.without()" in str(description)
    with pytest.raises(FrozenInstanceError):
        row.slot = "changed"


async def test_without_does_not_resolve_missing_dependencies_implicitly():
    class Provider(Plugin, provides=True):
        pass

    class Consumer(Plugin, needs=(Provider,)):
        pass

    agent = Agent(
        model=ScriptedModel([]),
        plugins=[PluginPack("library", [Provider(), Consumer()]).without(Provider)],
    )
    assert any(item.name == "Consumer" for item in agent.describe().plugins)
    with pytest.raises(PluginDependencyError, match="Consumer requires Provider"):
        async with agent.session():
            pytest.fail("Removing a provider must leave its consumer unresolved")
