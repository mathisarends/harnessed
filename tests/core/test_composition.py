from collections import UserList
from dataclasses import FrozenInstanceError

import pytest

from harnessed import Agent, Plugin, SystemPrompt
from harnessed.errors import PluginConflict, PluginDependencyError
from harnessed.kernel import Bundle, PluginScope
from harnessed.prompts import Instructions
from harnessed.runtime.composition import CompositionDescription
from harnessed.testing import ScriptedModel


class Choice(Plugin, slot="example.choice"):
    pass


class Extra(Plugin):
    pass


def test_composition_keeps_the_supplied_order():
    first, choice, last = Extra(name="first"), Choice(), Extra(name="last")
    resolved = Agent(plugins=[first, choice, last])
    assert resolved.plugins == (first, choice, last)
    assert [row.name for row in resolved.describe().plugins] == ["first", "Choice", "last"]


def test_composition_flattens_nested_groups_in_order():
    first = Extra(name="first")
    choice = Choice()
    shared = Extra(name="shared")
    last = Extra(name="last")
    group = [shared]
    inputs = iter([first, ([], [choice, (group,)]), group, last])

    resolved = Agent(plugins=inputs)

    assert resolved.plugins == (first, choice, shared, shared, last)
    assert [row.name for row in resolved.describe().plugins] == [
        "first",
        "Choice",
        "shared",
        "shared",
        "last",
    ]


def test_composition_flattens_groups_beyond_the_recursion_limit():
    plugin = Extra()
    group = [plugin]
    for _ in range(2_000):
        group = [group]
    assert Agent(plugins=group).plugins == (plugin,)


def test_composition_rejects_cyclic_groups():
    group = []
    group.append((group,))
    with pytest.raises(TypeError, match="Plugin groups cannot contain themselves"):
        Agent(plugins=group)


@pytest.mark.parametrize("swap", [False, True])
def test_duplicate_slots_are_errors_regardless_of_input_order(swap):
    inputs = [Choice(name="one"), Extra(), (Choice(name="two"),)]
    if swap:
        inputs.reverse()
    with pytest.raises(PluginConflict, match=r"example\.choice.*(one, two|two, one)"):
        Agent(plugins=inputs)


def test_unslotted_plugins_are_additive_even_when_they_provide_the_same_service():
    class Provider(Plugin, provides=True):
        pass

    first, second = Provider(), Provider()
    resolved = Agent(plugins=[first, second])
    assert resolved.plugins == (first, second)
    assert resolved.describe().plugins[0].provides == (Provider,)


def test_a_model_argument_and_a_model_service_conflict():
    from harnessed import Service
    from harnessed.models import Model

    with pytest.raises(PluginConflict, match="model"):
        Agent(model=ScriptedModel([]), plugins=[Service(Model, ScriptedModel([]))])


@pytest.mark.parametrize("invalid", [False, 42, [None], iter([Extra()]), UserList([Extra()])])
def test_composition_inputs_require_plugins(invalid):
    with pytest.raises(TypeError, match="Expected a Plugin instance"):
        Agent(plugins=[invalid])


def test_agent_rejects_kernel_recipes_and_disabled_entries():
    for value in [Bundle("hooks"), None, Extra]:
        with pytest.raises(TypeError, match="Plugin instance"):
            Agent(plugins=[value])


def test_callable_plugin_instances_keep_their_prompt_metadata():
    class CallablePlugin(Plugin):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt(Instructions(text="Keep this contribution."))

        def __call__(self):
            pass

    recipe = CallablePlugin()
    agent = Agent(plugins=[recipe])
    assert recipe in agent.plugins
    assert "Keep this contribution." in str(agent.prompt)


def test_description_is_static_structured_and_immutable():
    class Unmounted(Plugin, provides=True):
        async def setup(self, scope: PluginScope) -> None:
            pytest.fail("describe() must not mount plugins")

    agent = Agent(plugins=[Choice(), Unmounted()])
    description = agent.describe()
    assert isinstance(description, CompositionDescription)
    assert description is agent.describe()
    assert [row.name for row in description.plugins] == ["Choice", "Unmounted"]
    row = description.plugins[0]
    assert row.slot == "example.choice"
    assert "provides: Unmounted" in str(description)
    with pytest.raises(FrozenInstanceError):
        row.slot = "changed"


async def test_missing_providers_are_reported_instead_of_resolved_implicitly():
    class Provider(Plugin, provides=True):
        pass

    class Consumer(Plugin, needs=(Provider,)):
        pass

    agent = Agent(model=ScriptedModel([]), plugins=[([Consumer()],)])
    with pytest.raises(PluginDependencyError, match="Consumer requires Provider"):
        async with agent.session():
            pytest.fail("A consumer without its provider must not open")
