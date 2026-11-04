from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from harnessed import (
    Agent,
    Plugin,
    ToolAnnotations,
    ToolCall,
    ToolIcon,
    ToolInfo,
    ToolPresentation,
    tool,
)
from harnessed.core.model import ToolDefinition
from harnessed.core.tools.registry import ToolRegistry, ToolSpec
from harnessed.hooks import ExecuteTool
from harnessed.kernel import hook
from harnessed.plugins import Approvals
from harnessed.testing import ScriptedModel, call


async def test_decorator_captures_immutable_presentation_without_changing_model_or_permissions():
    tags = {"workspace"}
    meta = {"ui": {"labels": ["internal"]}}
    seen: list[ToolInfo] = []
    annotations = ToolAnnotations(read_only_hint=True, idempotent_hint=True)
    icon = ToolIcon("data:image/png;base64,aA==", mime_type="image/png", sizes=("24x24",))

    class Tools(Plugin):
        @tool(
            title="Inspect workspace", tags=tags, icons=(icon,), annotations=annotations, meta=meta
        )
        async def inspect(self, path: str) -> str:
            """Read the requested path."""
            return path

        @hook(ExecuteTool)
        async def capture(self, event, next_):
            seen.append(event.tool)
            return await next_()

    tags.add("later")
    meta["ui"]["labels"].append("later")
    model = ScriptedModel([call(Tools.inspect, path="document"), "done"])
    # The hint cannot bypass approval; allow execution explicitly for this test.
    async with Agent(
        model=model, plugins=[Tools(), Approvals(approver=lambda request: True)]
    ).session() as session:
        registry = session.ctx[ToolRegistry]
        assert not registry.is_readonly("inspect")
        result = await session.run("inspect")
    assert result.tool_calls[0][1].text == "document"
    presentation = seen[0].presentation
    assert presentation.title == "Inspect workspace"
    assert presentation.tags == frozenset({"workspace"})
    assert presentation.icons == (icon,)
    assert presentation.annotations == annotations
    assert presentation.meta["ui"]["labels"] == ("internal",)
    with pytest.raises(TypeError):
        presentation.meta["ui"]["labels"] = ()
    with pytest.raises(FrozenInstanceError):
        presentation.title = "changed"
    definition = model.requests[0].tools[0]
    assert definition == ToolDefinition(
        "inspect", "Read the requested path.", definition.parameters
    )
    assert set(definition.parameters["properties"]) == {"path"}
    assert "internal" not in str(model.requests)


@pytest.mark.parametrize("route", ["register", "decorator", "external"])
async def test_registration_routes_preserve_captured_presentation_after_replacement(route):
    async def work() -> str:
        return "done"

    async def external(arguments, context):
        pytest.fail("Only the captured registration is inspected")

    async with Agent(model=ScriptedModel(["unused"])).session() as session:
        registry = session.ctx[ToolRegistry]
        if route == "register":
            registry.register(work, title="Original", tags={route})
        elif route == "decorator":
            registry.tool(title="Original", tags={route})(work)
        else:
            registry.register_external(
                ToolDefinition("work", "", {}),
                external,
                owner=session.ctx,
                spec=ToolSpec(presentation=ToolPresentation(title="Original", tags={route})),
            )
        plan = registry.plan(ToolCall("work"))
        registry.register(work, override=True, title="Replacement")
        assert plan.tool.presentation.title == "Original"
        assert plan.tool.presentation.tags == frozenset({route})
        assert registry.plan(ToolCall("work")).tool.presentation.title == "Replacement"


@pytest.mark.parametrize(
    "options",
    [
        {"title": 1},
        {"tags": "workspace"},
        {"tags": {1}},
        {"tags": {""}},
        {"icons": ("icon.png",)},
        {"annotations": {"readOnlyHint": True}},
        {"meta": []},
        {"meta": {"invalid": object()}},
    ],
)
def test_invalid_presentation_is_rejected_at_declaration(options):
    with pytest.raises((TypeError, ValueError)):
        tool(**options)(lambda: "unused")


def test_annotation_and_icon_values_validate_their_contracts():
    with pytest.raises(ValidationError):
        ToolAnnotations(read_only_hint="true")
    with pytest.raises(ValidationError):
        ToolIcon("icon.png", theme="unknown")
