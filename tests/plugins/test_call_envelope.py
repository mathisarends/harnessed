import logging
from collections.abc import Mapping
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from harnessed import (
    Agent,
    Plugin,
    ToolCall,
    ToolContext,
    ToolEnvelope,
    ToolInfo,
    ToolResult,
    envelope_value,
    tool,
)
from harnessed.core.model import ToolDefinition
from harnessed.events import ToolCallRequestedEvent
from harnessed.hooks import ExecuteTool, ReviewToolResult, ToolPolicy
from harnessed.kernel import hook
from harnessed.plugins import CallEnvelope, EnvelopePosition, InvalidEnvelope
from harnessed.services import ToolRegistry
from harnessed.testing import ScriptedModel, call


class ActionLabel(BaseModel):
    action_label: Annotated[
        str,
        Field(max_length=60, description="Short present-tense summary of this call."),
    ]


class Rationale(BaseModel):
    rationale: str


class Intent(BaseModel):
    intent: str


class Workspace(Plugin):
    """A plugin whose tools know nothing about envelopes."""

    def __init__(self, received: list[Mapping[str, object]]) -> None:
        super().__init__()
        self.received = received

    @tool(readonly=True)
    async def read(self, path: str) -> str:
        self.received.append({"path": path})
        return f"read {path}"

    @tool
    async def write(self, path: str, text: str) -> str:
        self.received.append({"path": path, "text": text})
        return f"wrote {path}"


class Recorder(Plugin):
    """Records the envelope and tool info each tool hook observes."""

    def __init__(self, seen: list[tuple[str, ToolEnvelope, ToolInfo | None]]) -> None:
        super().__init__()
        self.seen = seen

    @hook(ToolPolicy)
    async def policy(self, event, next_):
        self.seen.append(("policy", event.envelope, event.tool))
        return await next_()

    @hook(ExecuteTool)
    async def execute(self, event, next_):
        self.seen.append(("execute", event.envelope, event.tool))
        return await next_()

    @hook(ReviewToolResult)
    async def review(self, event, next_):
        self.seen.append(("review", event.envelope, event.tool))
        return await next_()


def remote_definition(name: str = "remote", parameter: str = "path") -> ToolDefinition:
    return ToolDefinition(
        name,
        "",
        {
            "type": "object",
            "properties": {parameter: {"type": "string"}},
            "required": [parameter],
            "additionalProperties": False,
        },
    )


@pytest.mark.parametrize("envelope_first", [True, False], ids=["before", "after"])
async def test_call_envelope_reaches_tools_of_other_plugins_in_any_mount_order(envelope_first):
    received: list[Mapping[str, object]] = []
    seen: list[tuple[str, ToolEnvelope, ToolInfo | None]] = []
    envelope = CallEnvelope(ActionLabel)
    workspace = Workspace(received)
    plugins = [envelope, workspace] if envelope_first else [workspace, envelope]
    model = ScriptedModel([call("read", path="a.txt", action_label="Reading a.txt"), "done"])

    result = await Agent(model=model, plugins=[*plugins, Recorder(seen)]).run("Read a.txt")

    definitions = {definition.name: definition.parameters for definition in model.requests[0].tools}
    assert tuple(definitions["read"]["properties"]) == ("action_label", "path")
    assert tuple(definitions["write"]["properties"]) == ("action_label", "path", "text")
    assert definitions["write"]["required"] == ("action_label", "path", "text")
    assert received == [{"path": "a.txt"}]
    label = ActionLabel(action_label="Reading a.txt")
    assert [stage for stage, _, _ in seen] == ["policy", "execute", "review"]
    assert all(observed.get(ActionLabel) == label for _, observed, _ in seen)
    info = seen[0][2]
    assert info is not None and info.envelope == (ActionLabel,)
    requested = next(event for event in result.events if isinstance(event, ToolCallRequestedEvent))
    assert envelope_value(requested.call, ActionLabel) == label


async def test_call_envelope_reaches_external_tools_registered_before_and_after():
    received: list[Mapping[str, object]] = []

    async def remote(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        received.append(arguments)
        return ToolResult("remote")

    async with Agent(model=ScriptedModel(["unused"])).session() as session:
        registry = session.scope[ToolRegistry]
        registry.register_external(remote_definition("early"), remote, owner=session.scope)
        await session.mount(CallEnvelope(ActionLabel))
        registry.register_external(remote_definition("late"), remote, owner=session.scope)

        for name in ("early", "late"):
            assert tuple(session.tools[name].parameters["properties"]) == ("action_label", "path")
            plan = registry.plan(ToolCall(name, {"action_label": "Fetch", "path": "a"}))
            assert plan.envelope.get(ActionLabel) == ActionLabel(action_label="Fetch")
            assert (await registry.execute(plan.call, _expected=plan.binding)).text == "remote"

    assert received == [{"path": "a"}, {"path": "a"}]


@pytest.mark.parametrize(
    "label", [{}, {"action_label": 7}, {"action_label": "x" * 61}], ids=["missing", "type", "long"]
)
async def test_dropped_envelopes_let_the_call_run_without_a_value(label):
    received: list[Mapping[str, object]] = []
    seen: list[tuple[str, ToolEnvelope, ToolInfo | None]] = []
    model = ScriptedModel([call("read", path="a.txt", **label), "done"])
    plugins = [CallEnvelope(ActionLabel), Workspace(received), Recorder(seen)]

    result = await Agent(model=model, plugins=plugins).run("Read")

    assert received == [{"path": "a.txt"}]
    assert result.tool_calls[0][1].text == "read a.txt"
    assert all(observed.get(ActionLabel) is None for _, observed, _ in seen)
    assert envelope_value(result.tool_calls[0][0], ActionLabel) is None


async def test_erroring_envelopes_fail_the_call_like_invalid_arguments():
    received: list[Mapping[str, object]] = []
    model = ScriptedModel([call("read", path="a.txt"), "done"])
    plugins = [CallEnvelope(ActionLabel, on_invalid=InvalidEnvelope.ERROR), Workspace(received)]

    result = await Agent(model=model, plugins=plugins).run("Read")

    outcome = result.tool_calls[0][1]
    assert outcome.is_error and "ActionLabel" in outcome.text
    assert received == []


async def test_select_limits_the_envelope_and_envelopes_combine_in_registration_order():
    received: list[Mapping[str, object]] = []

    def mutating(info: ToolInfo) -> bool:
        return not info.readonly

    plugins = [
        CallEnvelope(ActionLabel),
        CallEnvelope(Rationale, select=mutating, on_invalid=InvalidEnvelope.ERROR),
        Workspace(received),
    ]
    async with Agent(model=ScriptedModel(["unused"]), plugins=plugins).session() as session:
        read = session.tools["read"].parameters
        write = session.tools["write"].parameters
        plan = session.scope[ToolRegistry].plan(
            ToolCall("write", {"action_label": "Saving", "rationale": "asked", "path": "a"})
        )

    assert tuple(read["properties"]) == ("action_label", "path")
    assert tuple(write["properties"]) == ("action_label", "rationale", "path", "text")
    assert plan.tool is not None and plan.tool.envelope == (ActionLabel, Rationale)
    assert plan.envelope.values == (
        ActionLabel(action_label="Saving"),
        Rationale(rationale="asked"),
    )


async def test_registry_envelopes_precede_a_tools_own_and_do_not_duplicate_it():
    class Browser(Plugin):
        @tool(envelope=(ActionLabel,))
        async def click(self, element: int) -> str:
            return str(element)

        @tool(envelope=(Rationale,))
        async def type_text(self, text: str) -> str:
            return text

    plugins = [CallEnvelope(ActionLabel), Browser()]
    async with Agent(model=ScriptedModel(["unused"]), plugins=plugins).session() as session:
        click = session.tools["click"].parameters
        typed = session.tools["type_text"].parameters
        registry = session.scope[ToolRegistry]
        missing = await registry.execute(ToolCall("click", {"element": 1}))

    assert tuple(click["properties"]) == ("action_label", "element")
    assert tuple(typed["properties"]) == ("action_label", "rationale", "text")
    # The tool's own declaration keeps its strict policy for the shared model.
    assert missing.is_error and "ActionLabel" in missing.text


async def test_trailing_envelopes_follow_the_tools_parameters_and_still_strip():
    class Browser(Plugin):
        @tool(envelope=(Rationale,))
        async def click(self, element: int) -> str:
            return str(element)

    plugins = [
        CallEnvelope(ActionLabel, position=EnvelopePosition.TRAILING),
        CallEnvelope(Intent),
        Browser(),
    ]
    async with Agent(model=ScriptedModel(["unused"]), plugins=plugins).session() as session:
        parameters = session.tools["click"].parameters
        registry = session.scope[ToolRegistry]
        arguments = {"intent": "open", "rationale": "asked", "element": 2, "action_label": "Open"}
        plan = registry.plan(ToolCall("click", arguments))
        result = await registry.execute(plan.call, _expected=plan.binding)

    assert tuple(parameters["properties"]) == ("intent", "rationale", "element", "action_label")
    assert parameters["required"] == ("intent", "rationale", "element", "action_label")
    assert plan.tool is not None and plan.tool.envelope == (Intent, Rationale, ActionLabel)
    assert plan.envelope.get(ActionLabel) == ActionLabel(action_label="Open")
    assert result.text == "2"


async def test_unmounting_removes_fields_but_keeps_the_planned_layout():
    received: list[Mapping[str, object]] = []
    async with Agent(
        model=ScriptedModel(["unused"]), plugins=[Workspace(received)]
    ).session() as session:
        handle = await session.mount(CallEnvelope(ActionLabel))
        registry = session.scope[ToolRegistry]
        plan = registry.plan(ToolCall("read", {"action_label": "Reading", "path": "a"}))
        await handle.aclose()

        assert tuple(session.tools["read"].parameters["properties"]) == ("path",)
        result = await registry.execute(plan.call, _expected=plan.binding)

    assert result.text == "read a"
    assert received == [{"path": "a"}]


class LabelledWorkspace(Plugin):
    @tool
    async def rename(self, action_label: str) -> str:
        return action_label


@pytest.mark.parametrize("envelope_first", [True, False], ids=["before", "after"])
async def test_python_tool_conflicts_fail_the_later_mount(envelope_first):
    async with Agent(model=ScriptedModel(["unused"])).session() as session:
        first, second = (
            (CallEnvelope(ActionLabel), LabelledWorkspace())
            if envelope_first
            else (LabelledWorkspace(), CallEnvelope(ActionLabel))
        )
        await session.mount(first)
        with pytest.raises(ValueError, match="conflicts with a parameter of tool 'rename'"):
            await session.mount(second)

        expected = ("action_label",) if not envelope_first else None
        if expected is not None:
            assert tuple(session.tools["rename"].parameters["properties"]) == expected
        else:
            assert "rename" not in session.tools


async def test_external_tool_conflicts_skip_the_envelope_with_a_warning(caplog):
    async def remote(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        return ToolResult(str(arguments["action_label"]))

    plugins = [CallEnvelope(ActionLabel)]
    async with Agent(model=ScriptedModel(["unused"]), plugins=plugins).session() as session:
        registry = session.scope[ToolRegistry]
        registry.register_external(
            remote_definition(parameter="action_label"), remote, owner=session.scope
        )
        with caplog.at_level(logging.WARNING, logger="harnessed.core.tools.envelope"):
            parameters = session.tools["remote"].parameters
        plan = registry.plan(ToolCall("remote", {"action_label": "mine"}))
        result = await registry.execute(plan.call, _expected=plan.binding)

    assert tuple(parameters["properties"]) == ("action_label",)
    assert "conflicts with a parameter of tool 'remote'" in caplog.text
    assert plan.tool is not None and plan.tool.envelope == ()
    assert result.text == "mine"


def test_envelope_value_reads_supplied_fields_and_ignores_calls_without_them():
    assert envelope_value(ToolCall("read", {"path": "a"}), ActionLabel) is None
    assert envelope_value(ToolCall("read", {"action_label": "Go"}), ActionLabel) == ActionLabel(
        action_label="Go"
    )


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"model": dict}, TypeError),
        ({"model": ActionLabel, "select": "readonly"}, TypeError),
        ({"model": ActionLabel, "on_invalid": "ignore"}, ValueError),
        ({"model": ActionLabel, "position": "middle"}, ValueError),
    ],
)
def test_call_envelope_validates_its_declaration(arguments, error):
    with pytest.raises(error):
        CallEnvelope(**arguments)
