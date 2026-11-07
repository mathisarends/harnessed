import pytest

from harnessed import Agent, Plugin, PluginValidation, tool
from harnessed.errors import PluginDependencyWarning
from harnessed.kernel import Inject
from harnessed.plugins import Service
from harnessed.services import SessionTools
from harnessed.testing import ScriptedModel


class Switch:
    def __init__(self) -> None:
        self.enabled = True


class Work(Plugin, needs=(Switch,)):
    def available(self, state: Inject[Switch]) -> bool:
        return state.enabled

    @tool(enabled=available, readonly=True, description="Original work")
    def work(self) -> str:
        return "done"


class Replacement(Plugin):
    @tool(name="work", override=True, readonly=True, description="Replacement work")
    def work(self) -> str:
        return "replacement"


async def test_session_tools_reflect_visibility_enabled_state_and_replacement():
    model = ScriptedModel(["done"])
    agent = Agent(model=model, plugins=[Work()], plugin_validation=PluginValidation.WARN)
    with pytest.warns(PluginDependencyWarning, match="Work requires Switch"):
        async with agent.session() as session:
            view = session.tools
            assert isinstance(view, SessionTools) and session.tools is view
            assert view.names == frozenset() and len(view) == 0
            assert "work" not in view and list(view) == []
            with pytest.raises(KeyError, match="work"):
                view["work"]
            switch = Switch()
            service = await session.mount(Service(Switch, switch))
            definition = view["work"]
            assert view.names == frozenset({"work"}) and len(view) == 1
            assert list(view) == ["work"] and list(view.values()) == [definition]
            assert list(view.items()) == [("work", definition)]
            await session.run("Inspect")
            assert model.requests[0].tools == tuple(view.values())
            with pytest.raises(TypeError):
                view["work"] = definition
            replacement = await session.mount(Replacement())
            assert view["work"].description == "Replacement work"
            await replacement.aclose()
            assert view["work"] == definition
            switch.enabled = False
            assert "work" not in view and view.names == frozenset()
            with pytest.raises(KeyError):
                view["work"]
            switch.enabled = True
            assert view["work"] == definition
            await service.aclose()
            assert view.names == frozenset()
