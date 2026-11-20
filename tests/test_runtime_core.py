import asyncio

import pytest
from pydantic import ValidationError

from harnessed import Agent, AgentLoop, Plugin, PolicyDecision, ToolStatus, tool
from harnessed.errors import ConfigError, PluginConflict
from harnessed.events import ModelRequestedEvent
from harnessed.models import ModelChunk, ModelInfo, ModelUsage
from harnessed.plugins import Approvals, Service
from harnessed.runtime.budget import ContextBudget
from harnessed.runtime.tokens import ApproximateTokenCounter, CalibratedTokenCounter, TokenCounter
from harnessed.runtime.tool_output_limit import ToolOutputLimit
from harnessed.testing import ScriptedModel, call


def test_composition_contains_exactly_the_configured_plugins():
    approvals = Approvals(default=PolicyDecision.ALLOW)
    app = Agent(
        model=ScriptedModel([]),
        plugins=[approvals],
        loop=AgentLoop(max_steps=9, max_tool_output=512),
    )
    assert [type(value) for value in app.plugins] == [Service, Approvals]
    assert app.plugins[1] is approvals
    assert [plugin.name for plugin in app.describe().plugins] == ["Service", "Approvals"]
    assert app.loop.max_steps == 9
    assert app.loop.max_tool_output == 512
    assert [type(value) for value in Agent().plugins] == []
    with pytest.raises(PluginConflict, match="approvals"):
        Agent(plugins=[Approvals(), Approvals()])
    with pytest.raises(ConfigError, match="AgentLoop"):
        Agent(loop=Approvals())  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValidationError):
        AgentLoop(max_steps=True)
    with pytest.raises(ValidationError):
        AgentLoop(max_tool_output=255)


async def test_session_limits_tool_output_from_loop_settings():
    class Large(Plugin):
        @tool(readonly=True)
        def dump(self) -> str:
            return "x" * 2_000

    model = ScriptedModel([call("dump"), "done"])
    agent = Agent(model=model, plugins=[Large()], loop=AgentLoop(max_tool_output=512))
    async with agent.session() as session:
        assert session.scope[ToolOutputLimit].max_chars == 512
        result = await session.run("dump")
    ((_, output),) = result.tool_calls
    assert output.truncation is not None
    assert output.truncation.max_chars == 512


@pytest.mark.parametrize(
    ("flags", "status"),
    [
        ({"readonly": True}, ToolStatus.OK),
        ({}, ToolStatus.OK),
        ({"confirm": True}, ToolStatus.UNAVAILABLE),
    ],
)
async def test_tools_run_without_an_approval_policy_unless_they_require_confirmation(flags, status):
    class Tools(Plugin):
        @tool(**flags)
        def act(self) -> str:
            return "acted"

    result = await Agent(model=ScriptedModel([call("act"), "done"]), plugins=[Tools()]).run("go")
    ((_, output),) = result.tool_calls
    assert output.status == status


async def test_confirmed_tools_ask_once_under_a_permissive_policy():
    questions = []

    def approve(request):
        questions.append(request.call.name)
        return True

    class Tools(Plugin):
        @tool(confirm=True)
        def act(self) -> str:
            return "acted"

    result = await Agent(
        model=ScriptedModel([call("act"), "done"]),
        plugins=[Tools(), Approvals(default=PolicyDecision.ALLOW, approver=approve)],
    ).run("go")
    ((_, output),) = result.tool_calls
    assert output.text == "acted"
    assert questions == ["act"]


async def test_unknown_model_window_and_async_token_counting_do_not_assume_128k():
    counts = []

    class Counter:
        def count_text(self, text):
            return len(text)

        async def count_request(self, request):
            await asyncio.sleep(0)
            counts.append(request)
            return 1

    model = ScriptedModel(["done"], info=ModelInfo("unknown"))
    app = Agent(
        model=model,
        plugins=[Service(TokenCounter, Counter()), Service(ContextBudget, ContextBudget(20))],
    )
    async with app.session() as session:
        await session.run("Go")
    assert len(counts) == 1


async def test_default_token_counter_calibrates_against_reported_input_tokens():
    model = ScriptedModel([[ModelChunk(text="ok"), ModelChunk(usage=ModelUsage(1_000, 1))]])
    async with Agent(model=model).session() as session:
        counter = session.scope[TokenCounter]
        text = "def handler(event): return event"
        assert counter.count_text(text) == ApproximateTokenCounter().count_text(text)
        await session.run("hello")
        request = session.last(ModelRequestedEvent).request
        estimate = ApproximateTokenCounter().count_request(request)
        assert counter.count_request(request) == min(1_000, estimate * 4)
        assert counter.count_text(text) > ApproximateTokenCounter().count_text(text)


def test_calibrated_token_counter_uses_a_supplied_falsy_estimator() -> None:
    class FalsyEstimator(ApproximateTokenCounter):
        def __bool__(self) -> bool:
            return False

        def count_text(self, text: str) -> int:
            return 23

    counter = CalibratedTokenCounter(FalsyEstimator())
    assert counter.count_text("custom estimate") == 23
