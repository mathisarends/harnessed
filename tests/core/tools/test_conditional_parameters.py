import inspect
import json
from dataclasses import dataclass
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from harnessed import Agent, Plugin, PluginConfig, ToolContext, tool
from harnessed.kernel import Inject, PluginScope, PluginState, Scope, needs
from harnessed.models import ToolCall
from harnessed.plugins import Service
from harnessed.testing import ScriptedModel, call
from harnessed.tools import ToolChain, ToolParameter, ToolRegistry


@dataclass
class Capability:
    multiple: bool = False


def supports_multiple(capability: Inject[Capability]) -> bool:
    return capability.multiple


type NewTab = Annotated[
    bool, Field(description="Open a new tab."), ToolParameter(enabled=supports_multiple)
]


class Navigation(Plugin):
    capability = needs(Capability)

    @tool(readonly=True, metadata=("navigation",), chain=ToolChain.CONTINUE)
    async def navigate(self, url: str, new_tab: NewTab = False) -> str:
        return f"{url}: {new_tab}"


@pytest.mark.parametrize("multiple", [False, True])
async def test_model_request_inspection_and_execution_share_the_effective_contract(multiple):
    response = call(Navigation.navigate, url="example", **({"new_tab": True} if multiple else {}))
    assert response.message.tool_calls[0].arguments == (
        {"url": "example", "new_tab": True} if multiple else {"url": "example"}
    )
    model = ScriptedModel([response, "done"])
    async with Agent(
        model=model, plugins=[Navigation(), Service(Capability, Capability(multiple))]
    ).session() as session:
        before = session.tools["navigate"]
        result = await session.run("Navigate")
        assert model.requests[0].tools == (before,)
        schema = before.parameters
        assert schema["required"] == ("url",)
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == ({"url", "new_tab"} if multiple else {"url"})
        if multiple:
            assert schema["$defs"]["NewTab"]["description"] == "Open a new tab."
            assert schema["properties"]["new_tab"]["default"] is False
        assert result.tool_calls[0][1].text == f"example: {multiple}"
        info = session.scope[ToolRegistry].plan(ToolCall("navigate", {"url": "example"})).tool
        assert info is not None and info.readonly and info.metadata.values == ("navigation",)
        assert inspect.signature(Navigation.navigate).parameters["new_tab"].default is False


@pytest.mark.parametrize("value", [False, True, None, "false", 0])
async def test_explicit_disabled_values_never_reach_the_callback(value):
    seen = []

    class Tools(Plugin):
        @tool(readonly=True)
        def action(self, flag: Annotated[bool, ToolParameter(enabled=False)] = False) -> str:
            seen.append(flag)
            return "called"

    model = ScriptedModel([call("action", flag=value), "done"])
    async with Agent(model=model, plugins=[Tools()]).session() as session:
        result = await session.run("Call")
        assert result.tool_calls[0][1].is_error
        assert "Disabled tool parameters" in result.tool_calls[0][1].text
        assert "flag" in result.tool_calls[0][1].text
        assert seen == []
        registry = session.scope[ToolRegistry]
        omitted = await registry.execute(ToolCall("action"))
        assert omitted.text == "called" and seen == [False]
        assert session.tools["action"].parameters["properties"] == {}
        assert "required" not in session.tools["action"].parameters


class LocalConfig(PluginConfig):
    multiple: bool = False


class LocalTools(Plugin[LocalConfig], provides=True):
    checks = PluginState(lambda: 0)
    ready = PluginState(lambda: False)

    async def setup(self, scope: PluginScope) -> None:
        self.ready = True

    def can_open(self) -> bool:
        assert self.ready
        self.checks += 1
        return self.config.multiple

    @tool(readonly=True)
    def action(self, flag: Annotated[bool, ToolParameter(enabled=can_open)] = False) -> bool:
        return flag


async def test_instance_conditions_run_after_setup_once_and_without_a_policy_service():
    recipe = LocalTools(config=LocalConfig(multiple=True))
    async with Agent(model=ScriptedModel([]), plugins=[recipe]).session() as session:
        assert "flag" in session.tools["action"].parameters["properties"]
        registry = session.scope[ToolRegistry]
        for _ in range(2):
            result = await registry.execute(ToolCall("action", {"flag": True}))
            assert result.text == "true"
        assert session.scope[LocalTools].checks == 1
        assert recipe.config.multiple


async def test_sessions_restart_disposal_and_prepared_calls_keep_activation_ownership():
    recipe = Navigation()
    agent = Agent(model=ScriptedModel([]))
    async with agent.session() as first, agent.session() as second:
        await first.mount(recipe)
        await second.mount(recipe)
        first_service = await first.mount(Service(Capability, Capability(False)))
        await second.mount(Service(Capability, Capability(True)))
        registry = first.scope[ToolRegistry]
        original_schema = first.tools["navigate"].parameters
        assert "new_tab" not in original_schema["properties"]
        assert "new_tab" in second.tools["navigate"].parameters["properties"]
        planned = registry.plan(ToolCall("navigate", {"url": "example"}))
        override = first.scope.provide(Capability, Capability(True))
        await first.scope.settle()
        assert "new_tab" in first.tools["navigate"].parameters["properties"]
        assert "new_tab" not in original_schema["properties"]
        rejected = registry.check_enabled(planned, scope=first.scope)
        assert rejected is not None and "registration changed" in rejected.text
        await override()
        await first.scope.settle()
        assert "new_tab" not in first.tools["navigate"].parameters["properties"]
        await first_service.aclose()
        assert "navigate" not in first.tools
        assert "new_tab" in second.tools["navigate"].parameters["properties"]


@pytest.mark.parametrize("enabled", [False, True])
def test_conditional_parameters_always_require_a_default(enabled):
    def action(flag: Annotated[bool, ToolParameter(enabled=enabled)]) -> bool:
        return flag

    with pytest.raises(TypeError, match=r"flag.*requires a default"):
        ToolRegistry(Scope()).register(action)


def test_field_without_a_default_is_not_a_usable_conditional_default():
    required = Field(description="Required")

    def action(flag: Annotated[bool, ToolParameter(enabled=False)] = required) -> bool:
        return flag

    with pytest.raises(TypeError, match=r"flag.*requires a default"):
        ToolRegistry(Scope()).register(action)


async def test_disabled_defaults_still_use_existing_validation_and_per_call_factories():
    defaults = Field(default_factory=list)

    async with Scope() as scope:
        registry = ToolRegistry(scope)

        @registry.tool
        def collect(values: Annotated[list[int], ToolParameter(enabled=False)] = defaults) -> int:
            values.append(1)
            return len(values)

        first = await registry.execute(ToolCall("collect"))
        second = await registry.execute(ToolCall("collect"))
        assert first.text == second.text == "1"

        @registry.tool
        def invalid(flag: Annotated[bool, ToolParameter(enabled=False)] = "bad") -> str:
            pytest.fail("Invalid default reached callback")

        result = await registry.execute(ToolCall("invalid"))
        assert result.is_error and "ValidationError" in result.text


@pytest.mark.parametrize("present", [False, True])
async def test_required_and_optional_predicate_injection_and_missing_services(present):
    def optional(capability: Inject[Capability | None]) -> bool:
        return capability is None or capability.multiple

    async with Scope() as scope:
        if present:
            scope.provide(Capability, Capability(True))
        registry = ToolRegistry(scope)

        @registry.tool
        def required(flag: Annotated[bool, ToolParameter(enabled=supports_multiple)] = False):
            return flag

        @registry.tool
        def optional_tool(flag: Annotated[bool, ToolParameter(enabled=optional)] = False):
            return flag

        definitions = {
            definition.name: definition.parameters for definition in registry.definitions
        }
        assert ("flag" in definitions["required"]["properties"]) is present
        assert "flag" in definitions["optional_tool"]["properties"]
        # Availability is fixed even when the registry scope later gains a service.
        scope.provide(Capability, Capability(not present))
        assert registry.definitions[0].parameters == definitions["required"]


@pytest.mark.parametrize("predicate", [None, 1, "yes", lambda: 1, lambda: None])
def test_invalid_predicates_fail_at_metadata_or_registration(predicate):
    with pytest.raises(TypeError, match="bool"):
        marker = ToolParameter(enabled=predicate)

        def action(flag: Annotated[bool, marker] = False) -> bool:
            return flag

        ToolRegistry(Scope()).register(action)


@pytest.mark.parametrize("case", ["argument", "exception", "async", "awaitable"])
def test_invalid_predicate_signatures_exceptions_and_awaitable_results_fail_activation(case):
    def model_argument(value: bool) -> bool:
        return value

    def broken() -> bool:
        raise RuntimeError("broken predicate")

    async def asynchronous() -> bool:
        return True

    def awaitable():
        return asynchronous()

    predicate, error, match = {
        "argument": (model_argument, TypeError, "Inject"),
        "exception": (broken, RuntimeError, "broken predicate"),
        "async": (asynchronous, TypeError, "synchronous"),
        "awaitable": (awaitable, TypeError, "synchronous"),
    }[case]
    with pytest.raises(error, match=match):
        marker = ToolParameter(enabled=predicate)

        def action(flag: Annotated[bool, marker] = False) -> bool:
            return flag

        ToolRegistry(Scope()).register(action)


@pytest.mark.parametrize("enabled", [False, True])
async def test_enabled_constraints_aliases_and_whole_tool_conditions_are_preserved(enabled):
    type Count = Annotated[int, Field(ge=1, description="Count"), ToolParameter(enabled=enabled)]

    async with Scope() as scope:
        registry = ToolRegistry(scope)

        @registry.tool
        def action(model_dump: Count = 2) -> int:
            return model_dump

        schema = registry.definitions[0].parameters
        if enabled:
            assert schema["$defs"]["Count"]["minimum"] == 1
            assert schema["$defs"]["Count"]["description"] == "Count"
        valid = await registry.execute(ToolCall("action", {"model_dump": 3} if enabled else {}))
        assert valid.text == ("3" if enabled else "2")
        invalid = await registry.execute(ToolCall("action", {"model_dump": 0}))
        assert invalid.is_error
        registry.register(action, name="hidden", enabled=False)
        assert [definition.name for definition in registry.definitions] == ["action"]
        assert (await registry.execute(ToolCall("hidden"))).status == "unavailable"


@pytest.mark.parametrize("nested", [False, True])
def test_params_models_reject_markers_including_nested_aliased_fields(nested):
    class Inner(BaseModel):
        flag: Annotated[bool, ToolParameter(enabled=False)] = Field(False, alias="flagAlias")

    class Outer(BaseModel):
        inner: Inner

    params = Outer if nested else Inner

    def action(request: params) -> str:
        return "called"

    with pytest.raises(TypeError, match=r"signature parameters.*params model fields"):
        ToolRegistry(Scope()).register(action, params=params)


async def test_envelopes_cannot_claim_disabled_arguments_before_or_after_registration():
    class Collision(BaseModel):
        other: bool = Field(False, alias="flag")

    class Label(BaseModel):
        action_label: str

    def action(flag: Annotated[bool, ToolParameter(enabled=False)] = False) -> bool:
        return flag

    async with Scope() as scope:
        registry = ToolRegistry(scope)
        with pytest.raises(ValueError, match=r"conflicts.*parameter"):
            registry.register(action, envelope=(Collision,))
        registry.envelope(Collision)
        with pytest.raises(ValueError, match=r"conflicts.*parameter"):
            registry.register(action)

    async with Scope() as scope:
        registry = ToolRegistry(scope)
        registry.register(action)
        with pytest.raises(ValueError, match=r"conflicts.*parameter"):
            registry.envelope(Collision)
        registry.envelope(Label)
        assert set(registry.definitions[0].parameters["properties"]) == {"action_label"}
        result = await registry.execute(ToolCall("action", {"action_label": "Run"}))
        assert result.text == "false"
        rejected = await registry.execute(
            ToolCall("action", {"action_label": "Run", "flag": False})
        )
        assert rejected.is_error


def test_duplicate_nested_and_injected_markers_are_rejected_clearly():
    type Hidden = Annotated[bool, ToolParameter(enabled=False)]

    def duplicate(flag: Annotated[Hidden, ToolParameter(enabled=True)] = False) -> bool:
        return flag

    default_flags = Field(default_factory=list)

    def nested(flags: list[Hidden] = default_flags) -> list[bool]:
        return flags

    def injected(flag: Annotated[Inject[bool], ToolParameter(enabled=False)]) -> bool:
        return flag

    registry = ToolRegistry(Scope())
    with pytest.raises(TypeError, match="only one ToolParameter"):
        registry.register(duplicate)
    with pytest.raises(TypeError, match="not a nested value"):
        registry.register(nested)
    with pytest.raises(TypeError, match="injected parameter"):
        registry.register(injected)


def test_per_call_context_and_async_callable_predicates_cannot_configure_parameters():
    def per_call(context: Inject[ToolContext]) -> bool:
        return not context.cancelled

    class AsyncPredicate:
        async def __call__(self) -> bool:
            return True

    def contextual(flag: Annotated[bool, ToolParameter(enabled=per_call)] = False) -> bool:
        return flag

    def asynchronous(
        flag: Annotated[bool, ToolParameter(enabled=AsyncPredicate())] = False,
    ) -> bool:
        return flag

    registry = ToolRegistry(Scope())
    with pytest.raises(TypeError, match="per-call ToolContext"):
        registry.register(contextual)
    with pytest.raises(TypeError, match="synchronous"):
        registry.register(asynchronous)


def test_params_model_parameter_itself_cannot_be_hidden():
    class Params(BaseModel):
        query: str

    def search(request: Annotated[Params, ToolParameter(enabled=False)]) -> str:
        return request.query

    with pytest.raises(TypeError, match="not params models"):
        ToolRegistry(Scope()).register(search, params=Params)


async def test_ordinary_recursive_aliases_keep_working():
    type Tree = int | list[Tree]

    async with Scope() as scope:
        registry = ToolRegistry(scope)

        @registry.tool
        def tree(value: Tree) -> Tree:
            return value

        result = await registry.execute(ToolCall("tree", {"value": [1, [2]]}))
        assert json.loads(result.text) == [1, [2]]
