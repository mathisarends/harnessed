from collections.abc import Mapping
from typing import Annotated

import pytest
from pydantic import BaseModel, Field, RootModel
from pydantic.json_schema import JsonSchemaValue, model_json_schema

from harnessed import Agent, Plugin, ToolCall, ToolContext, ToolEnvelope, ToolInfo, ToolResult, tool
from harnessed.events.codec import decode_event, encode_event
from harnessed.hooks import ExecuteTool, ReviewToolResult, ToolPolicy
from harnessed.kernel import hook
from harnessed.models import ToolDefinition
from harnessed.testing import ScriptedModel, call
from harnessed.tools import ToolRegistry, ToolSpec


class ActionLabel(BaseModel):
    action_label: Annotated[str, Field(description="Short user-facing summary of this action.")]


class Reason(BaseModel):
    reason: str = "unspecified"


class Search(BaseModel):
    query: str


def browser_plugin(
    clicked: list[int], seen: list[tuple[str, ToolEnvelope, ToolInfo | None]]
) -> Plugin:
    class Browser(Plugin):
        @tool(readonly=True, envelope=(ActionLabel,))
        async def click(self, element: int) -> str:
            clicked.append(element)
            return f"clicked {element}"

        @hook(ToolPolicy)
        async def policy(self, event, next_):
            seen.append(("policy", event.envelope, event.tool))
            return await next_()

        @hook(ExecuteTool)
        async def execute(self, event, next_):
            seen.append(("execute", event.envelope, event.tool))
            return await next_()

        @hook(ReviewToolResult)
        async def review(self, event, next_):
            seen.append(("review", event.envelope, event.tool))
            return await next_()

    return Browser()


async def test_envelope_fields_lead_the_schema_reach_hooks_and_never_reach_the_callback():
    clicked: list[int] = []
    seen: list[tuple[str, ToolEnvelope, ToolInfo | None]] = []
    model = ScriptedModel([call("click", element=3, action_label="Open the pricing page"), "done"])

    result = await Agent(model=model, plugins=[browser_plugin(clicked, seen)]).run("Open pricing")

    parameters = model.requests[0].tools[0].parameters
    assert tuple(parameters["properties"]) == ("action_label", "element")
    assert parameters["required"] == ("action_label", "element")
    assert parameters["properties"]["action_label"]["description"] == (
        "Short user-facing summary of this action."
    )
    assert clicked == [3]
    assert [stage for stage, _, _ in seen] == ["policy", "execute", "review"]
    label = ActionLabel(action_label="Open the pricing page")
    assert all(envelope.get(ActionLabel) == label for _, envelope, _ in seen)
    info = seen[0][2]
    assert info is not None and info.envelope == (ActionLabel,)
    assert tuple(info.definition.parameters["properties"]) == ("action_label", "element")
    finished_call, finished = result.tool_calls[0]
    assert finished.text == "clicked 3"
    assert finished_call.arguments == {"element": 3, "action_label": "Open the pricing page"}
    assert all(decode_event(encode_event(event)) == event for event in result.events)


@pytest.mark.parametrize(
    "arguments", [{"element": 3}, {"element": 3, "action_label": 7}], ids=["missing", "invalid"]
)
async def test_missing_or_invalid_envelopes_fail_like_invalid_arguments(arguments):
    clicked: list[int] = []
    seen: list[tuple[str, ToolEnvelope, ToolInfo | None]] = []
    model = ScriptedModel([call("click", **arguments), "done"])

    result = await Agent(model=model, plugins=[browser_plugin(clicked, seen)]).run("Click")

    outcome = result.tool_calls[0][1]
    assert outcome.is_error
    assert "Invalid arguments for click" in outcome.text and "ActionLabel" in outcome.text
    assert clicked == []
    assert seen == []


async def test_envelopes_apply_to_parameter_models_and_external_tools():
    received: list[Mapping[str, object]] = []

    async def search(params: Search) -> str:
        return params.query

    async def remote(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        received.append(arguments)
        return ToolResult("remote")

    async with Agent(model=ScriptedModel(["unused"])).session() as session:
        registry = session.scope[ToolRegistry]
        registry.register(search, params=Search, envelope=(ActionLabel,))
        registry.register_external(
            ToolDefinition(
                "remote",
                "",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            remote,
            owner=session.scope,
            spec=ToolSpec(envelope=(ActionLabel, Reason)),
        )
        external = session.tools["remote"].parameters
        assert tuple(external["properties"]) == ("action_label", "reason", "path")
        assert external["required"] == ("action_label", "path")
        assert external["additionalProperties"] is False
        assert tuple(session.tools["search"].parameters["properties"]) == ("action_label", "query")

        searched = await registry.execute(
            ToolCall("search", {"action_label": "Find shoes", "query": "shoes"})
        )
        remote_call = ToolCall("remote", {"action_label": "Read", "path": "a.txt"})
        plan = registry.plan(remote_call)
        await registry.execute(remote_call, _expected=plan.binding)

    assert searched.text == "shoes"
    assert received == [{"path": "a.txt"}]
    assert plan.envelope.get(Reason) == Reason()
    assert plan.envelope.get(ActionLabel) == ActionLabel(action_label="Read")


async def test_envelope_schema_is_isolated_from_model_owned_schema() -> None:
    class SearchLabel(BaseModel):
        label: Search

        @classmethod
        def model_json_schema(cls, *_args: object, **_kwargs: object) -> JsonSchemaValue:
            return schema

    schema = model_json_schema(SearchLabel)
    label_schema = schema["properties"]["label"]
    search_schema = schema["$defs"]["Search"]

    class LabelledSearch(Plugin):
        @tool(readonly=True, envelope=(SearchLabel,))
        async def lookup(self, criteria: Search) -> str:
            return criteria.query

    label_schema["description"] = "Changed after declaration"
    search_schema["required"].append("changed")

    async with Agent(
        model=ScriptedModel(["unused"]), plugins=[LabelledSearch()]
    ).session() as session:
        parameters = session.tools["lookup"].parameters
        assert parameters["properties"]["label"] == {"$ref": "#/$defs/Search"}
        assert parameters["properties"]["criteria"] == {"$ref": "#/$defs/Search"}
        assert parameters["$defs"]["Search"]["required"] == ("query",)


class OtherLabel(BaseModel):
    action_label: str


async def test_envelope_fields_conflicting_with_tool_parameters_reject_the_mount():
    class Labelled(Plugin):
        @tool(readonly=True, envelope=(ActionLabel,))
        async def rename(self, action_label: str) -> str:
            return action_label

    async with Agent(model=ScriptedModel(["unused"])).session() as session:
        with pytest.raises(ValueError, match="conflicts with a parameter of tool 'rename'"):
            await session.mount(Labelled())
        assert "rename" not in session.tools


async def test_envelope_fields_conflicting_with_each_other_are_rejected_at_registration():
    async def work(value: int) -> str:
        return str(value)

    async with Agent(model=ScriptedModel(["unused"])).session() as session:
        registry = session.scope[ToolRegistry]
        with pytest.raises(ValueError, match="conflicts with another envelope of tool 'work'"):
            registry.register(work, envelope=(ActionLabel, OtherLabel))
        assert "work" not in session.tools


class Empty(BaseModel):
    pass


@pytest.mark.parametrize(
    ("envelope", "error"),
    [
        ((dict,), TypeError),
        ((RootModel[str],), TypeError),
        ((Empty,), TypeError),
        ((ActionLabel, ActionLabel), ValueError),
    ],
)
def test_envelope_declarations_require_distinct_field_models(envelope, error):
    with pytest.raises(error):
        ToolSpec(envelope=envelope)
