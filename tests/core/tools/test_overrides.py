from typing import Annotated

import pytest
from pydantic import BaseModel, Field, field_validator

from harnessed import Agent, Plugin, ToolContext, tool
from harnessed.kernel import Inject
from harnessed.testing import ScriptedModel, call
from harnessed.tools import Param


class Gestures(Plugin):
    @tool(description="Drag")
    def drag(
        self,
        duration_seconds: Annotated[float, Field(gt=0, le=5)] = 0.5,
        *,
        context: Inject[ToolContext],
    ) -> float:
        return duration_seconds


async def test_recipe_override_reaches_inspection_model_validation_and_callback():
    base = Gestures()
    touch = base.with_tool(base.drag, description="Swipe").with_tool(
        Gestures.drag,
        parameters={"duration_seconds": Param(default=0.15, le=1, description="Swipe duration")},
    )
    model = ScriptedModel([call(touch.drag), call("drag", duration_seconds=2), "done"])
    async with Agent(model=model, plugins=[touch]).session() as session:
        info = session.tools.resolve(touch.drag)
        assert info.definition == session.tools.explain("drag").definition
        result = await session.run("Swipe")
        assert model.requests[0].tools == (info.definition,)
        assert info.definition.description == "Swipe"
        field = info.definition.parameters["properties"]["duration_seconds"]
        assert field["default"] == 0.15
        assert field["maximum"] == 1
        assert field["exclusiveMinimum"] == 0
        assert field["description"] == "Swipe duration"
        assert result.tool_calls[0][1].text == "0.15"
        assert result.tool_calls[1][1].is_error

    async with Agent(model=ScriptedModel([]), plugins=[base]).session() as session:
        assert session.tools["drag"].description == "Drag"
        assert session.tools["drag"].parameters["properties"]["duration_seconds"]["default"] == 0.5


@pytest.mark.parametrize("invalid", ["duration_secs", "context", "self"])
def test_unknown_override_fails_on_the_recipe(invalid):
    with pytest.raises(ValueError, match=f"Unknown parameter '{invalid}'") as error:
        Gestures().with_tool(Gestures.drag, parameters={invalid: Param(default=0.15)})
    if invalid == "duration_secs":
        assert "Did you mean 'duration_seconds'?" in str(error.value)


@pytest.mark.parametrize("patch", [Param(le=6), Param(ge=0), Param(ge=2, le=1), Param(le=0.1)])
def test_invalid_overrides_fail_during_configuration(patch):
    with pytest.raises(ValueError):
        Gestures().with_tool(Gestures.drag, parameters={"duration_seconds": patch})


async def test_activation_fields_and_chained_overrides():
    class Wheel(Plugin):
        def fields(self):
            return {"steps": Field(le=5)}

        @tool(parameter_fields=fields)
        def scroll(self, steps: int = 3) -> int:
            return steps

    recipe = Wheel().with_tool(Wheel.scroll, parameters={"steps": Param(default=1, ge=1)})
    configured = recipe.with_tool(Wheel.scroll, parameters={"steps": Param(description="Steps")})
    async with Agent(model=ScriptedModel([]), plugins=[configured]).session() as session:
        field = session.tools["scroll"].parameters["properties"]["steps"]
        assert (field["default"], field["minimum"], field["maximum"]) == (1, 1, 5)
        assert field["description"] == "Steps"

    invalid = recipe.with_tool(Wheel.scroll, parameters={"steps": Param(le=6)})
    with pytest.raises(ValueError, match="widen"):
        async with Agent(model=ScriptedModel([]), plugins=[invalid]).session():
            pytest.fail("A widened activation-time bound was accepted")


class SwipeOptions(BaseModel):
    distance: int = Field(alias="pixels", ge=1)

    @field_validator("distance")
    @classmethod
    def even(cls, value: int) -> int:
        if value % 2:
            raise ValueError("Expected an even distance")
        return value


class Touchscreen(Plugin):
    @tool(params=SwipeOptions)
    def swipe(self, options: SwipeOptions) -> int:
        return options.distance


async def test_parameter_model_preserves_alias_and_validator_with_new_default():
    recipe = Touchscreen().with_tool(
        Touchscreen.swipe, parameters={"pixels": Param(default=4, ge=2, le=8)}
    )
    model = ScriptedModel([call(recipe.swipe), call("swipe", pixels=3), "done"])
    result = await Agent(model=model, plugins=[recipe]).run("Swipe")
    assert result.tool_calls[0][1].text == "4"
    assert result.tool_calls[1][1].is_error
    assert "required" not in model.requests[0].tools[0].parameters


async def test_default_passes_through_the_parameter_model_validator():
    invalid = Touchscreen().with_tool(Touchscreen.swipe, parameters={"pixels": Param(default=3)})
    result = await Agent(model=ScriptedModel([call("swipe"), "done"]), plugins=[invalid]).run("Go")
    assert result.tool_calls[0][1].is_error
    assert "even distance" in result.tool_calls[0][1].text
