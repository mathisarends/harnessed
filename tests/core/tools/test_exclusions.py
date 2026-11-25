import pytest

from harnessed import Agent, Plugin, tool
from harnessed.testing import ScriptedModel
from harnessed.tools import ToolAvailability
from harnessed.tools.references import tool_reference


class Pointer(Plugin):
    @tool
    def click(self) -> str:
        return "clicked"

    @tool(name="drag_at")
    def drag(self) -> str:
        return "dragged"


class Trackpad(Pointer):
    @tool(enabled=False)
    def scroll(self) -> str:
        return "scrolled"


async def test_recipe_exclusions_compose_and_only_selected_tools_reach_the_model():
    recipe = Trackpad()
    selected = recipe.without_tools(recipe.drag)
    configured = selected.without_tools(tool_reference(Trackpad.scroll)).without_instructions()
    model = ScriptedModel(["done"])
    async with Agent(model=model, plugins=[configured]).session() as session:
        assert session.tools.names == {"click"}
        assert session.tools.explain("scroll").availability is ToolAvailability.EXCLUDED
        assert session.tools.explain("drag_at").definition is None
        await session.run("Use the pointer")
        assert [definition.name for definition in model.requests[0].tools] == ["click"]

    async with Agent(model=model, plugins=[recipe]).session() as session:
        assert session.tools.names == {"click", "drag_at"}
        assert session.tools.explain("scroll").availability is ToolAvailability.DISABLED


async def test_exclusions_release_names_and_follow_the_mount_lifetime():
    reduced = Pointer().without_tools(Pointer.click, Pointer.drag)
    async with Agent(model=ScriptedModel([])).session() as session:
        view = session.tools
        async with session.mount(reduced):
            assert view.explain("drag_at").availability is ToolAvailability.EXCLUDED
            assert view.explain(Pointer.drag) == view.explain("drag_at")
            full = Pointer(name="full")
            async with session.mount(full):
                assert view.explain(reduced.drag).availability is ToolAvailability.EXCLUDED
                assert view.explain(full.drag).name == "drag_at"
                assert view.explain(full.drag) == view.explain("drag_at")
                assert view.explain("drag_at").availability is ToolAvailability.AVAILABLE
            assert view.explain("drag_at").availability is ToolAvailability.EXCLUDED
            assert view.explain(full.drag).availability is ToolAvailability.NOT_REGISTERED
        assert view.explain(reduced.drag).availability is ToolAvailability.NOT_REGISTERED
        assert view.explain("drag_at").availability is ToolAvailability.NOT_REGISTERED


async def test_excluded_declarations_skip_activation_time_schema_building():
    def fields(owner):
        raise AssertionError("Excluded tool built its parameters")

    class Dynamic(Plugin):
        @tool(parameter_fields=fields)
        def search(self, query: str) -> str:
            return query

    recipe = Dynamic().without_tools(Dynamic.search)
    async with Agent(model=ScriptedModel([]), plugins=[recipe]).session() as session:
        assert session.tools.explain("search").availability is ToolAvailability.EXCLUDED
        assert "search" not in session.tools


def test_recipe_exclusions_reject_foreign_shadowed_and_undecorated_methods():
    class CustomPointer(Pointer):
        @tool
        def drag(self) -> str:
            return "custom"

    recipe = CustomPointer()
    for foreign in (Pointer.drag, CustomPointer().click, Trackpad.scroll):
        with pytest.raises(ValueError, match="does not belong"):
            recipe.without_tools(foreign)
    for invalid in ("drag", Plugin.instructions):
        with pytest.raises(TypeError, match="@tool method"):
            recipe.without_tools(invalid)
