import asyncio
import json

import pytest
from pydantic import BaseModel, Field

from harnessed import Agent, Plugin, SectionDelivery, ToolStatus, hook, tool
from harnessed.errors import OutputUnavailable, OutputValidationError
from harnessed.events import (
    ControlState,
    OutputValidatedEvent,
    OutputValidationFailedEvent,
    SessionControlChangedEvent,
    TextDeltaEvent,
    ToolCallFinishedEvent,
    TurnStartedEvent,
)
from harnessed.hooks import Next, OutputDecision, PrepareOutput, TurnStopping
from harnessed.integrations.json import JsonSessionStore
from harnessed.kernel import PluginScope
from harnessed.models import ModelChunk, ModelInfo, ModelUsage, StopReason
from harnessed.plugins import AgentLoop, FinalStepPolicy
from harnessed.testing import CallbackModel, ScriptedModel, call, reply


class Answer(BaseModel):
    count: int = Field(gt=0)


class Phased(Plugin):
    @tool(readonly=True)
    async def done(self) -> str:
        return "Return the final answer now."

    @hook(PrepareOutput)
    async def output(self, event: PrepareOutput, next_: Next[OutputDecision]) -> OutputDecision:
        turn = event.session.log.current_turn()
        if not any(
            isinstance(item, ToolCallFinishedEvent)
            and item.call.name == "done"
            and item.result.status is ToolStatus.OK
            for item in turn.events
        ):
            return OutputDecision.DEFER
        return await next_()

    @hook(TurnStopping)
    def continue_work(self, event: TurnStopping) -> bool | None:
        if not any(
            isinstance(item, ToolCallFinishedEvent) and item.call.name == "done"
            for item in event.session.log.current_turn().events
        ):
            return True
        return None


def answer(native: bool, count: int):
    return json.dumps({"count": count}) if native else call("final_answer", count=count)


@pytest.mark.parametrize("native", [False, True])
async def test_progress_streams_before_native_output_and_correction(native: bool) -> None:
    model = ScriptedModel(
        ["Working", call("done"), answer(native, 0), answer(native, 2)],
        info=ModelInfo("test", supports_structured_output=native),
    )
    async with Agent(model=model, plugins=[Phased()]).session() as session:
        async with session.stream("Count", output=Answer) as stream:
            events = [event async for event in stream]
        result = events[-1].result
        assert result.output == Answer(count=2)
        assert any(
            isinstance(event, TextDeltaEvent) and event.text == "Working" for event in events
        )
        start = next(event for event in result.events if isinstance(event, TurnStartedEvent))
        assert start.output_schema is not None
    assert all(request.output_schema is None for request in model.requests[:2])
    assert all("final_answer" not in {tool.name for tool in r.tools} for r in model.requests[:2])
    assert all(request.output_schema is not None for request in model.requests[2:])
    failures = [event for event in result.events if isinstance(event, OutputValidationFailedEvent)]
    assert len(failures) == 1 and failures[0].attempt == 1


async def test_deferred_steps_do_not_bypass_retry_limits() -> None:
    model = ScriptedModel(["Working", call("done"), answer(False, 0)])
    with pytest.raises(OutputValidationError):
        await Agent(model=model, plugins=[Phased(), AgentLoop(output_retries=0)]).run(
            "Count", output=Answer
        )
    assert len(model.requests) == 3


async def test_deferred_progress_still_consumes_the_token_budget() -> None:
    model = ScriptedModel([reply("Working", usage=ModelUsage(2, 2)), call("done")])
    result = await Agent(model=model, plugins=[Phased()]).run(
        "Count", output=Answer, token_budget=4
    )
    assert result.stop_reason is StopReason.TOKEN_BUDGET
    assert len(model.requests) == 1


async def test_concurrent_sessions_and_runtime_removal_are_isolated() -> None:
    phased_model = ScriptedModel(["Working", call("done"), answer(False, 2), answer(False, 3)])
    plain_model = ScriptedModel([answer(False, 1)])
    async with (
        Agent(model=phased_model).session() as phased,
        Agent(model=plain_model).session() as plain,
    ):
        handle = await phased.mount(Phased(), section_delivery=SectionDelivery.IGNORE)
        first, second = await asyncio.gather(
            phased.run("Phased", output=Answer), plain.run("Plain", output=Answer)
        )
        assert first.output.count == 2 and second.output.count == 1
        await handle.aclose()
        third = await phased.run("Native", output=Answer)
        assert third.output.count == 3
        assert phased_model.requests[-1].output_schema is not None
    assert plain_model.requests[0].output_schema is not None


async def test_failed_startup_leaves_no_output_policy() -> None:
    class Broken(Phased):
        async def setup(self, scope: PluginScope) -> None:
            raise ValueError("failed setup")

    model = ScriptedModel([answer(False, 1)])
    async with Agent(model=model).session() as session:
        with pytest.raises(ValueError, match="failed setup"):
            await session.mount(Broken(), section_delivery=SectionDelivery.IGNORE)
        assert (await session.run("Native", output=Answer)).output.count == 1


@pytest.mark.parametrize("summary", [False, True])
async def test_step_limits_remain_incomplete_without_validated_output(summary: bool) -> None:
    model = ScriptedModel(["Still working", "Summary"])
    result = await Agent(
        model=model,
        plugins=[
            Phased(),
            AgentLoop(final_step=FinalStepPolicy.SUMMARIZE if summary else FinalStepPolicy.NONE),
        ],
    ).run("Count", output=Answer, max_steps=2)
    assert not result.completed
    assert not any(isinstance(event, OutputValidatedEvent) for event in result.events)
    with pytest.raises(OutputUnavailable):
        result.require_output()
    assert model.requests[-1].output_schema is None
    if summary:
        assert model.requests[-1].tools == ()


async def test_paused_finalization_restores_the_declared_output_contract(tmp_path) -> None:
    entered, release, pausing = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def respond(_request):
        entered.set()
        await asyncio.wait_for(release.wait(), 5)
        yield ModelChunk(tool_call=call("done").message.tool_calls[0])

    def changed(event: SessionControlChangedEvent) -> None:
        if event.state is ControlState.PAUSING:
            pausing.set()

    store = JsonSessionStore(root=tmp_path / "sessions")
    async with Agent(model=CallbackModel(respond), plugins=[Phased()], store=store).session(
        id="phased"
    ) as session:
        session.on(SessionControlChangedEvent, changed)
        await session.submit("Count", output=Answer, max_steps=3)
        await asyncio.wait_for(entered.wait(), 5)
        pause = asyncio.create_task(session.pause())
        try:
            await asyncio.wait_for(pausing.wait(), 5)
        finally:
            release.set()
        await asyncio.wait_for(pause, 5)
        assert session.last(ToolCallFinishedEvent).call.name == "done"

    model = ScriptedModel([answer(False, 4)])
    async with Agent(model=model, plugins=[Phased()], store=store).session(
        id="phased", restore_outputs=[Answer]
    ) as session:
        await session.resume()
        await asyncio.wait_for(session.wait_until_idle(), 5)
        assert session.last(OutputValidatedEvent).data == {"count": 4}
    assert len(model.requests) == 1 and model.requests[0].output_schema is not None
