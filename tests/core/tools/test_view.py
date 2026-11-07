from typing import Annotated

import pytest

from harnessed import Agent, Plugin, PluginValidation, ToolContext, tool
from harnessed.errors import PluginDependencyWarning
from harnessed.kernel import Inject, InjectMarker, Key
from harnessed.plugins import Service
from harnessed.services import SessionTools, ToolAvailability
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
            assert view.explain("work").availability is ToolAvailability.NOT_REGISTERED
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
            switch.enabled = False
            assert view.explain("work").availability is ToolAvailability.AVAILABLE
            await replacement.aclose()
            assert view.explain("work").availability is ToolAvailability.DISABLED
            switch.enabled = True
            assert view["work"] == definition
            switch.enabled = False
            assert "work" not in view and view.names == frozenset()
            with pytest.raises(KeyError):
                view["work"]
            switch.enabled = True
            assert view["work"] == definition
            await service.aclose()
            assert view.names == frozenset()


async def test_tool_explanation_tracks_call_and_predicate_services_without_restarting_plugin():
    starts = []

    class Dynamic(Plugin):
        async def setup(self) -> None:
            starts.append(self)

        def allowed(self, state: Inject[Switch]) -> bool:
            return state.enabled

        @tool(enabled=allowed)
        def work(
            self,
            text: str,
            service: Inject[int],
            *,
            ctx: Inject[ToolContext],
            optional: Inject[float | None],
        ) -> str:
            return text

    model = ScriptedModel(["before", "after"])
    async with Agent(model=model, plugins=[Dynamic()]).session() as session:
        snapshot = session.tools.explain("work")
        assert snapshot.availability is ToolAvailability.MISSING_SERVICES
        assert snapshot.missing_services == (Switch, int)
        assert snapshot.definition is not None
        assert list(snapshot.definition.parameters["properties"]) == ["text"]
        await session.run("before")
        assert model.requests[0].tools == ()
        switch = Switch()
        async with session.use(Service(Switch, switch)):
            assert session.tools.explain("work").missing_services == (int,)
            async with session.use(Service(int, 1)):
                assert session.tools.explain("work").availability is ToolAvailability.AVAILABLE
                await session.run("after")
                assert model.requests[1].tools == tuple(session.tools.values())
                switch.enabled = False
                disabled = session.tools.explain("work")
                assert disabled.availability is ToolAvailability.DISABLED
                assert disabled.missing_services == () and "work" not in session.tools
        assert session.tools.explain("work").missing_services == (Switch, int)
        assert len(starts) == 1
        assert snapshot.missing_services == (Switch, int)
        assert session.tools.explain("unknown").availability is ToolAvailability.NOT_REGISTERED


async def test_named_predicate_requirements_and_disabled_overrides_are_explained():
    gate = Key[bool]("application.gate")

    def enabled(allowed: Annotated[bool, InjectMarker(gate)]) -> bool:
        return allowed

    class Named(Plugin):
        @tool(name="work", enabled=enabled, override=True)
        def unavailable(self) -> str:
            return "disabled"

    async with Agent(model=ScriptedModel([]), plugins=[Replacement()]).session() as session:
        async with session.use(Named()):
            missing = session.tools.explain("work")
            assert missing.availability is ToolAvailability.MISSING_SERVICES
            assert missing.missing_services == (gate,)
            async with session.use(Service(gate, False)):
                explanation = session.tools.explain("work")
                assert explanation.availability is ToolAvailability.DISABLED
                async with session.use(Service(gate, True)):
                    assert session.tools.explain("work").availability is ToolAvailability.AVAILABLE
            assert explanation.definition is not None
            assert "work" not in session.tools
        assert session.tools.explain("work").availability is ToolAvailability.AVAILABLE


async def test_tool_explanations_propagate_predicate_failures():
    def broken() -> bool:
        raise ValueError("predicate failed")

    class Invalid(Plugin):
        @tool(enabled=broken)
        def work(self) -> str:
            return "unused"

    async with Agent(model=ScriptedModel([]), plugins=[Invalid()]).session() as session:
        with pytest.raises(ValueError, match="predicate failed"):
            session.tools.explain("work")
