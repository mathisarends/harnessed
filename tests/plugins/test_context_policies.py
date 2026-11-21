import asyncio
import json

import pytest

from harnessed import Agent as Application
from harnessed import Approvals, Compaction, Context, Decision, Plugin, tool
from harnessed.context import ContextMeter, ContextState, at_ratio, every_turns
from harnessed.context.compaction import (
    CompactionContext,
    CompactionRequest,
    ModelSummarizer,
    Summary,
)
from harnessed.context.tokens import ApproximateTokenCounter
from harnessed.events import (
    CompactionAppliedEvent,
    ModelUsageRecordedEvent,
    ObservationPolicyAppliedEvent,
    SpillCreatedEvent,
    SystemMessageEvent,
    TurnEndedEvent,
    TurnStartedEvent,
    UserMessageEvent,
)
from harnessed.events.codec import decode_event, encode_event
from harnessed.hooks import PrepareRequest
from harnessed.kernel import Next, hook, needs
from harnessed.models import (
    AssistantMessage,
    ModelChunk,
    ModelInfo,
    ModelRequest,
    ModelUsage,
    ToolCall,
    ToolResult,
    ToolStatus,
    UserMessage,
)
from harnessed.plugins import LatestObservationOnly
from harnessed.plugins.spill import Spill, SpillStore
from harnessed.runtime.hooks import PreStep
from harnessed.sessions.files import SessionFiles
from harnessed.sessions.log import SessionLog
from harnessed.testing import ExtractiveSummarizer, ScriptedModel
from harnessed.tools import ToolError
from tests._audit import Audit, AuditProjection


async def test_large_results_spill_to_session_files_and_keep_structured_metadata(tmp_path):
    original = "output\n" * 4000

    class ToolsPlugin(Plugin):
        @tool
        async def large(self) -> ToolResult:
            return ToolResult(original, ToolStatus.ERROR, exit_code=3)

    tools = ToolsPlugin(name="large")

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("large"))], "done"])
    spill = Spill(threshold_tokens=100, preview_tokens=8)
    app = Application(
        model=model, approvals=Approvals(default=Decision.ALLOW), plugins=[tools, spill]
    )
    async with app.session() as agent:
        agent_audit = Audit(agent)
        result = (await agent.run("go")).tool_calls[0][1]
        assert result.exit_code == 3 and result.status == ToolStatus.ERROR
        assert result.spill is not None and result.content != original
        files = agent.scope[SessionFiles]
        chunks = [
            await files.read(result.spill.path, offset=offset, limit=16000)
            for offset in range(0, len(original), 16000)
        ]
        assert "".join(chunks) == original
        assert "read_spill" in result.content and len(result.content) < len(original)
        assert {t.name for t in model.requests[0].tools} == {"large", "read_spill"}
        assert model.requests[1].messages[-1].text == result.content
        assert result.with_note("extra").spill == result.spill
        events = tuple(
            decode_event(json.loads(json.dumps(encode_event(e)))) for e in agent_audit.events
        )
        assert AuditProjection(events).messages == agent.log.messages
        assert any(isinstance(e, SpillCreatedEvent) for e in events)
        ref = result.spill
        state = agent.snapshot()
    spill_2 = Spill()
    async with Application(
        model=ScriptedModel([]),
        plugins=[spill_2],
    ).session(state=state) as restored:
        assert await restored.scope[SpillStore].read(ref.path, offset=7, limit=6) == "output"


async def test_session_spill_reads_unicode_ranges_reject_escape_and_reuse_content(tmp_path):
    spill = Spill()
    async with Application(
        model=ScriptedModel([]),
        plugins=[spill],
    ).session() as session:
        store = session.scope[SpillStore]
        refs = await asyncio.gather(*(store.put("ä😀abc") for _ in range(20)))
        first = refs[0]
        assert all(ref == first for ref in refs)
        assert await store.read(first.path, offset=1, limit=2) == "😀a"
        assert await store.read(first.path, offset=100) == ""
        with pytest.raises(ToolError, match="does not belong"):
            await store.read(str(tmp_path.parent / "outside.txt"))
        with pytest.raises(ValueError):
            await store.read(first.path, limit=16001)


async def test_read_spill_tool_returns_bounded_ranges_without_respilling():
    model = ScriptedModel([])
    spill_5 = Spill(threshold_tokens=1)
    async with Application(
        model=model,
        plugins=[spill_5],
    ).session() as agent:
        agent_audit = Audit(agent)
        ref = await agent.scope[SpillStore].put("x" * 100)
        model.responses.extend(
            [
                [ModelChunk(tool_call=ToolCall("read_spill", {"path": ref.path, "limit": 40}))],
                "done",
            ]
        )
        result = (await agent.run("read")).tool_calls[0][1]
        assert result.content == "x" * 40 and result.spill is None
        assert not any(isinstance(e, SpillCreatedEvent) for e in agent_audit.events)


async def test_threshold_compaction_commits_once_and_replays_and_forks(tmp_path):

    class ToolsPlugin(Plugin):
        @tool
        async def work(self) -> str:
            return "ok"

    tools = ToolsPlugin(name="work")

    model = ScriptedModel(["initial reply", [ModelChunk(tool_call=ToolCall("work"))], "done"])
    harness = Application(
        model=model,
        approvals=Approvals(default=Decision.ALLOW),
        plugins=[tools],
        context=Context(
            max_tokens=400,
            compaction=Compaction(
                trigger=at_ratio(0.5),
                summarizer=ExtractiveSummarizer(),
                keep_last_steps=1,
                max_summary_tokens=24,
            ),
        ),
    )
    async with harness.session() as agent:
        agent_audit = Audit(agent)
        await agent.run("old context " + "x" * 1200)
        await agent.run("current task\nTicket: preserve current context")
        applied = [e for e in agent_audit.events if isinstance(e, CompactionAppliedEvent)]
        assert len(applied) == 1
        request = model.requests[2]
        texts = [m.text for m in request.messages]
        assert any(text.startswith("Conversation summary:") for text in texts)
        assert "current task\nTicket: preserve current context" in texts
        assert "x" * 1200 not in "\n".join(texts)
        replay = AuditProjection(agent_audit.events)
        assert replay.messages == agent.log.messages
        assert any(
            isinstance(e, UserMessageEvent) and "x" * 1200 in e.message.text
            for e in agent_audit.events
        )


async def test_compaction_preserves_base_prompt_recent_turns_and_replaces_previous_summary():
    session = SessionLog("projection")
    await session.append(SystemMessageEvent("permanent rules"))
    for name in ("one", "two"):
        await session.append(TurnStartedEvent(name))
        await session.append(UserMessageEvent(UserMessage(name)))
        await session.append(TurnEndedEvent(name))
    await session.append(
        CompactionAppliedEvent(4, "first summary", before_tokens=0, after_tokens=0)
    )
    assert [m.text for m in session.messages] == [
        "permanent rules",
        "Conversation summary:\nfirst summary",
        "two",
    ]
    await session.append(
        CompactionAppliedEvent(7, "combined summary", before_tokens=0, after_tokens=0)
    )
    assert [m.text for m in session.messages] == [
        "permanent rules",
        "Conversation summary:\ncombined summary",
    ]
    with pytest.raises(ValueError, match="advance"):
        await session.append(
            CompactionAppliedEvent(7, "duplicate", before_tokens=0, after_tokens=0)
        )
    with pytest.raises(ValueError, match="completed context boundary"):
        await session.append(CompactionAppliedEvent(2, "invalid", before_tokens=0, after_tokens=0))


async def test_failed_or_cancelled_compactor_does_not_commit_partial_compaction():
    started = asyncio.Event()

    class BlockingCompactor:
        async def summarize(self, request):
            started.set()
            await asyncio.Event().wait()

    model = ScriptedModel(["first reply", "kept"])
    harness = Application(
        model=model,
        approvals=Approvals(default=Decision.ALLOW),
        context=Context(
            max_tokens=500,
            compaction=Compaction(
                trigger=at_ratio(0.5), summarizer=BlockingCompactor(), keep_last_steps=1
            ),
        ),
    )
    async with harness.session() as agent:
        agent_audit = Audit(agent)
        await agent.run("x" * 1000)
        await agent.run("kept")
        await agent.submit("next")
        await asyncio.wait_for(started.wait(), timeout=5)
        await agent.abort()
        assert not any(isinstance(e, CompactionAppliedEvent) for e in agent_audit.events)
        assert "x" * 1000 in "\n".join(m.text for m in agent.log.messages)


async def test_compactor_error_is_reported_and_history_is_intact():

    class Broken:
        async def summarize(self, request):
            raise ValueError("summary failed")

    model = ScriptedModel(["reply", "kept"])
    harness = Application(
        model=model,
        approvals=Approvals(default=Decision.ALLOW),
        context=Context(
            max_tokens=500,
            compaction=Compaction(trigger=at_ratio(0.5), summarizer=Broken(), keep_last_steps=1),
        ),
    )
    async with harness.session() as agent:
        agent_audit = Audit(agent)
        await agent.run("x" * 1000)
        await agent.run("kept")
        with pytest.raises(ValueError, match="summary failed"):
            await agent.run("next")
        assert not any(isinstance(e, CompactionAppliedEvent) for e in agent_audit.events)
        assert len(model.requests) == 2


async def test_keep_turns_retains_recent_work_and_later_compaction_merges_old_summary():
    model = ScriptedModel(["reply"] * 4)
    harness = Application(
        model=model,
        approvals=Approvals(default=Decision.ALLOW),
        context=Context(
            max_tokens=600,
            compaction=Compaction(
                trigger=every_turns(1),
                summarizer=ExtractiveSummarizer(),
                keep_last_steps=1,
                max_summary_tokens=16,
            ),
        ),
    )
    async with harness.session() as agent:
        agent_audit = Audit(agent)
        await agent.run("FIRST " + "x" * 800)
        await agent.run("SECOND " + "y" * 800)
        await agent.run("current")
        texts = [m.text for m in model.requests[-1].messages]
        assert any("y" * 800 in text for text in texts)
        assert not any("x" * 800 in text for text in texts)
        await agent.run("next")
        assert sum(isinstance(e, CompactionAppliedEvent) for e in agent_audit.events) == 2
        assert sum(m.id.startswith("compaction:") for m in agent.log.messages) == 1


async def test_compaction_does_not_commit_a_summary_that_expands_the_request():

    class Verbose:
        async def summarize(self, request):
            return Summary("z" * 2000)

    model = ScriptedModel(["reply", "reply"])
    harness = Application(
        model=model,
        approvals=Approvals(default=Decision.ALLOW),
        context=Context(
            max_tokens=100,
            compaction=Compaction(
                trigger=at_ratio(0.5),
                summarizer=Verbose(),
                keep_last_steps=1,
                max_summary_tokens=2000,
            ),
        ),
    )
    async with harness.session() as agent:
        agent_audit = Audit(agent)
        await agent.run("original" * 30)
        await agent.run("next")
        assert not any(isinstance(e, CompactionAppliedEvent) for e in agent_audit.events)


async def test_latest_observation_policy_keeps_one_unchanged_snapshot_and_unwinds():

    class Observer(Plugin):
        def __init__(self):
            super().__init__()
            self.index = 0

        @hook(PreStep)
        async def observe(self, event, next_):
            self.index += 1
            event.session.observe(UserMessage(f"full {self.index}"), source="browser")
            return await next_()

    model = ScriptedModel(["one", "two", "three"])
    async with Application(
        model=model,
        approvals=Approvals(default=Decision.ALLOW),
        plugins=[Observer(), LatestObservationOnly()],
    ).session() as agent:
        agent_audit = Audit(agent)
        for text in ("one", "two", "three"):
            await agent.run(text)
        texts = [m.text for m in model.requests[-1].messages]
        assert "full 1" not in texts and "full 2" not in texts and "full 3" in texts
        assert AuditProjection(agent_audit.events).messages == agent.log.messages
    assert isinstance(agent.log.events[-1], ObservationPolicyAppliedEvent)
    assert agent.log.events[-1].max_observations is None


async def test_cache_usage_is_preserved_as_provider_reported_usage():
    usage = ModelUsage(100, 10, 80, 20)
    model = ScriptedModel([[ModelChunk(text="one", usage=usage)]])
    async with Application(
        model=model, approvals=Approvals(default=Decision.ALLOW)
    ).session() as agent:
        await agent.run("one")
        event = agent.log.last(ModelUsageRecordedEvent)
        assert event.usage.cache_hit_rate == 0.8
        assert decode_event(encode_event(event)) == event


async def test_model_summarizer_uses_explicit_model_without_tools_and_closes_stream():
    llm = ScriptedModel(["summary"])
    summary = await ModelSummarizer(llm, counter=ApproximateTokenCounter()).summarize(
        CompactionRequest(
            CompactionContext(
                (UserMessage("history"),), (), (), ContextState(100, 0, 1000, 1), None
            ),
            32,
        )
    )
    assert summary.text == "summary"
    assert not llm.requests[0].tools and llm.requests[0].settings.max_tokens == 32


async def test_model_summarizer_keeps_tool_names_and_arguments_in_summary_input():
    llm = ScriptedModel(["summary"])
    messages = (AssistantMessage(tool_calls=(ToolCall("bash", {"command": "pytest tests/auth"}),)),)
    await ModelSummarizer(llm, counter=ApproximateTokenCounter()).summarize(
        CompactionRequest(
            CompactionContext(messages, (), (), ContextState(100, 0, 1000, 1), None), 32
        )
    )
    transcript = llm.requests[0].messages[-1].text
    assert "bash" in transcript and "pytest tests/auth" in transcript


@pytest.mark.parametrize(
    "create",
    [
        lambda: Compaction(trigger=at_ratio(0), summarizer=ExtractiveSummarizer()),
        lambda: Compaction(trigger=at_ratio(1.1), summarizer=ExtractiveSummarizer()),
        lambda: Compaction(
            trigger=at_ratio(0.8), summarizer=ExtractiveSummarizer(), keep_last_steps=-1
        ),
        lambda: Spill(threshold_tokens=0),
        lambda: ObservationPolicyAppliedEvent(0),
        lambda: ModelUsage(-1),
    ],
)
def test_strategy_configuration_is_validated(create):
    with pytest.raises(ValueError):
        create()


@pytest.mark.parametrize(
    ("window", "max_tokens", "effective"),
    [(None, 2_000, 2_000), (128_000, 4_000, 4_000), (None, None, None)],
)
async def test_plugins_measure_requests_against_the_effective_context_window(
    window, max_tokens, effective
):
    measured: list[ContextState] = []

    class Gauge(Plugin):
        meter = needs(ContextMeter)

        @hook(PrepareRequest)
        async def prepare(self, _event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
            request = await next_()
            measured.append(await self.meter.measure(request))
            return request

    model = ScriptedModel(["done"], info=ModelInfo("scripted", context_window=window))
    agent = Application(
        model=model, plugins=[Gauge()], context=Context(max_tokens=max_tokens, reserve_tokens=64)
    )
    await agent.run("Measure this request")
    [state] = measured
    assert state.context_window == effective
    assert state.input_tokens > 0 and state.reserve_tokens == 64
    assert state.turns_since_compaction == 0
    if effective is None:
        assert state.ratio is None
    else:
        assert state.ratio == (state.input_tokens + 64) / effective
