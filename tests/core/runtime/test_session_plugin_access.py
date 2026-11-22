from typing import assert_type, override

import pytest

from harnessed import Agent, Plugin, PluginScope, PluginState, PluginValidation, hook, needs
from harnessed.errors import SessionClosed
from harnessed.hooks import Next, PreStep, StepDecision
from harnessed.kernel import Scope
from harnessed.models import ModelChunk, ModelUsage
from harnessed.testing import ScriptedModel


class Recorder(Plugin):
    steps = PluginState(list[str])

    @hook(PreStep)
    async def record(self, event: PreStep, next_: Next[StepDecision]) -> StepDecision:
        self.steps.append("step")
        return await next_()


class NamedRecorder(Recorder):
    pass


class Waiting(Plugin):
    backend = needs(bytes)


async def test_session_returns_the_activation_holding_runtime_data():
    recipe = Recorder()
    async with Agent(model=ScriptedModel(["done"]), plugins=[recipe]).session() as session:
        await session.run("Go")

        activation = session.plugin(Recorder)

        assert_type(activation, Recorder)
        assert_type(session.plugin(recipe), Recorder)
        assert activation is not recipe
        assert activation.recipe is recipe
        assert activation.steps == ["step"]


async def test_recipe_selects_one_of_several_activations_of_a_class():
    first = Recorder(name="first")
    second = Recorder(name="second")
    async with Agent(model=ScriptedModel([]), plugins=[first, second]).session() as session:
        assert session.plugin(second).recipe is second
        with pytest.raises(LookupError, match="pass the mounted recipe"):
            session.plugin(Recorder)


async def test_class_lookup_matches_a_subclass_activation():
    recipe = NamedRecorder()
    async with Agent(model=ScriptedModel([]), plugins=[recipe]).session() as session:
        assert session.plugin(Recorder).recipe is recipe


@pytest.mark.filterwarnings("ignore:Agent composition is incomplete")
async def test_plugin_without_an_activation_is_a_lookup_error():
    agent = Agent(
        model=ScriptedModel([]), plugins=[Waiting()], plugin_validation=PluginValidation.WARN
    )
    async with agent.session() as session:
        with pytest.raises(LookupError, match="No active Waiting"):
            session.plugin(Waiting)
        with pytest.raises(LookupError, match="No active Recorder"):
            session.plugin(Recorder)


async def test_closed_session_rejects_plugin_access_and_keeps_usage():
    model = ScriptedModel([[ModelChunk(text="done", usage=ModelUsage(7, 2))]])
    async with Agent(model=model, plugins=[Recorder()]).session() as session:
        await session.run("Go")

    with pytest.raises(SessionClosed):
        session.plugin(Recorder)
    assert session.usage == ModelUsage(7, 2)


async def test_scope_lists_only_active_class_plugins():
    class Feature(Plugin):
        @override
        async def setup(self, scope: PluginScope) -> None:
            return None

    async with Scope() as root:
        active = await root.plugin(Feature())
        await root.plugin(Waiting())

        assert root.active_plugins(Plugin) == (active.instance,)
        assert root.active_plugins(Waiting) == ()

        await active.aclose()

        assert root.active_plugins(Plugin) == ()
