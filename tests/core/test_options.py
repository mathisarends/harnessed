import pytest
from pydantic import TypeAdapter, ValidationError

from harnessed.context.budget import ContextBudget
from harnessed.kernel import Scope
from harnessed.models import GenerationSettings, ModelInfo, ModelRequest, ModelUsage
from harnessed.runtime.options import AgentLoopConfig
from harnessed.tools import ToolRegistry


@pytest.mark.parametrize("rule", ["publish", "", lambda: None])
def test_approval_selectors_are_typed(rule):
    from harnessed import Approvals

    with pytest.raises(TypeError):
        Approvals(ask=[rule])


@pytest.mark.parametrize(
    "options",
    [
        {"max_steps": True},
        {"max_steps": "2"},
        {"max_tokens": 0},
        {"context_window": -1},
        {"max_parallel_tools": 0},
        {"tool_timeout": float("inf")},
        {"on_max_tokens": "guess"},
    ],
)
def test_loop_options_are_strict_and_declarative(options):
    with pytest.raises(ValidationError):
        AgentLoopConfig(**options)


@pytest.mark.parametrize("options", [{"limit": 0}, {"reserve_tokens": -1}, {"limit": True}])
def test_context_budget_options_are_strict(options):
    with pytest.raises(ValidationError):
        ContextBudget(**options)


@pytest.mark.parametrize("timeout", [0, -1, True, "1", float("nan"), float("inf")])
def test_tool_timeouts_reject_invalid_values_at_both_configuration_boundaries(timeout):
    with pytest.raises(ValidationError):
        AgentLoopConfig(tool_timeout=timeout)
    registry = ToolRegistry(Scope())
    with pytest.raises(ValidationError):
        registry.register(lambda: None, timeout_seconds=timeout)


def test_neutral_model_metadata_and_request_constraints_are_schema_visible():
    assert (
        TypeAdapter(GenerationSettings).json_schema()["properties"]["max_tokens"]["anyOf"][0][
            "minimum"
        ]
        == 1
    )
    for construct in (
        lambda: ModelInfo(""),
        lambda: ModelInfo("model", context_window=0),
        lambda: ModelInfo("model", supports_images="yes"),
        lambda: ModelRequest((), settings=GenerationSettings(max_tokens=True)),
        lambda: ModelUsage(-1),
        lambda: ModelUsage(1, cost=float("nan")),
    ):
        with pytest.raises(ValidationError):
            construct()
