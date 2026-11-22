import asyncio

import pytest
from pydantic import ValidationError

from harnessed import Agent, AgentLoop, Approvals, Context, Decision, Plugin, Service, tool
from harnessed.context import TokenCounter
from harnessed.context.budget import ContextBudget
from harnessed.context.tokens import ApproximateTokenCounter, CalibratedTokenCounter
from harnessed.context.tool_output_limit import ToolOutputLimit
from harnessed.errors import ConfigError
from harnessed.events import ModelRequestedEvent
from harnessed.models import ModelChunk, ModelInfo, ModelUsage, ToolStatus
from harnessed.testing import ScriptedModel, call


def test_composition_contains_exactly_the_configured_plugins():
    approvals = Approvals(default=Decision.ALLOW)
    app = Agent(
        model=ScriptedModel([]),
        loop=AgentLoop(max_steps=9),
        approvals=approvals,
        context=Context(max_tool_output_chars=512),
    )
    assert app.plugins == ()
    assert app.approvals is approvals
    assert [plugin.name for plugin in app.describe().plugins] == []
    assert app.loop.max_steps == 9
    assert app.context.max_tool_output_chars == 512
    assert [type(value) for value in Agent().plugins] == []
    with pytest.raises(TypeError, match="Plugin instance"):
        Agent(approvals=Approvals(), plugins=[Approvals()])
    with pytest.raises(ConfigError, match="AgentLoop"):
        Agent(loop=Approvals())  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValidationError):
        AgentLoop(max_steps=True)
    with pytest.raises(ValidationError):
        Context(max_tool_output_chars=255)


async def test_session_limits_tool_output_from_loop_settings():
    class Large(Plugin):
        @tool(readonly=True)
        def dump(self) -> str:
            return "x" * 2_000

    model = ScriptedModel([call("dump"), "done"])
    agent = Agent(model=model, plugins=[Large()], context=Context(max_tool_output_chars=512))
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
    ],
)
async def test_default_policy_allows_tools_without_a_responder(flags, status):
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
        @tool
        def act(self) -> str:
            return "acted"

    result = await Agent(
        model=ScriptedModel([call("act"), "done"]),
        approvals=Approvals(default=Decision.ALLOW, ask=[Tools.act], approver=approve),
        plugins=[Tools()],
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
