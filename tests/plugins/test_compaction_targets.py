import pytest

from harnessed import Agent
from harnessed.core.budget import ContextBudget
from harnessed.core.errors import ConfigError
from harnessed.core.events.messages import MessageAppendedEvent
from harnessed.core.messages import AssistantMessage, UserMessage
from harnessed.core.model import ModelInfo, ModelRequest, ModelResponse, ModelUsage, StopReason
from harnessed.core.plugin import Service
from harnessed.core.tokens import CalibratedTokenCounter, TokenCounter
from harnessed.events import (
    CompactionFinishedEvent,
    CompactionStartedEvent,
    ModelUsageRecordedEvent,
)
from harnessed.plugins import Compaction
from harnessed.services import CompactionRequest, Summary
from harnessed.testing import ScriptedModel
from tests._audit import Audit


class Characters:
    def count_text(self, text: str) -> int:
        return len(text)

    def count_request(self, request) -> int:
        return (
            7
            + sum(10 + len(message.text) for message in request.messages)
            + 13 * len(request.tools)
        )


class RecordingSummary:
    def __init__(self, *texts: str) -> None:
        self.texts = iter(texts)
        self.requests: list[CompactionRequest] = []

    async def summarize(self, request: CompactionRequest) -> Summary:
        self.requests.append(request)
        return Summary(next(self.texts), ModelUsage(11, 3))


@pytest.mark.parametrize("target", [{"target_tokens": 180}, {"target_ratio": 0.18}, {}])
async def test_targets_measure_complete_request_and_capture_reserve(target):
    summaries = RecordingSummary("done")
    model = ScriptedModel(["old", "answer"], info=ModelInfo("test", context_window=1000))
    async with Agent(
        model=model,
        instructions="RULES",
        plugins=[
            Service(TokenCounter, Characters()),
            Service(ContextBudget, ContextBudget(reserve_tokens=100)),
            Compaction(trigger=lambda _: False, summarizer=summaries, keep_last_steps=1, **target),
        ],
    ).session() as session:
        await session.run("x" * 500)
        await session.run("kept")
        original = session.messages
        audit = Audit(session)
        notices = []
        session.on(CompactionStartedEvent, notices.append)
        session.on(CompactionFinishedEvent, notices.append)
        applied = await session.compact()
        assert applied is not None
        expected_before = 7 + sum(10 + len(m.text) for m in original)
        expected_after = 7 + sum(10 + len(m.text) for m in session.messages)
        assert (applied.before_tokens, applied.after_tokens) == (expected_before, expected_after)
        assert applied.before_ratio == expected_before / 1000
        assert applied.after_ratio == expected_after / 1000
        assert applied.freed_tokens == expected_before - expected_after
        assert applied.reserve_tokens == 100
        assert applied.target_tokens == (180 if target else None)
        assert applied.target_reached is (True if target else None)
        assert len(notices) == 2
        assert notices[0].before_tokens == expected_before
        assert notices[1].after_tokens == expected_after
        assert notices[1].freed_tokens == applied.freed_tokens
        context = summaries.requests[0].context
        assert [m.text for m in context.history_to_compact] == ["x" * 500, "old"]
        assert [m.text for m in context.retained_context] == ["kept", "answer"]
        assert [m.text for m in context.instructions] == ["RULES"]
        assert context.state.input_tokens == expected_before
        assert context.state.reserve_tokens == 100
        assert len([e for e in audit.events if isinstance(e, ModelUsageRecordedEvent)]) == 1


@pytest.mark.parametrize(
    "texts,expected,calls",
    [
        (("long summary", "short"), "short", 2),
        (("x" * 40, "y" * 30), "y" * 30, 2),
        (("short",), "short", 1),
    ],
)
async def test_target_retry_is_bounded_and_keeps_best_complete_candidate(texts, expected, calls):
    summaries = RecordingSummary(*texts)
    async with Agent(
        model=ScriptedModel(["old", "kept"]),
        plugins=[
            Service(TokenCounter, Characters()),
            Compaction(
                trigger=lambda _: False, summarizer=summaries, keep_last_steps=1, target_tokens=76
            ),
        ],
    ).session() as session:
        await session.run("x" * 500)
        await session.run("task")
        audit = Audit(session)
        applied = await session.compact()
        assert applied is not None and applied.summary == expected
        assert len(summaries.requests) == calls
        assert applied.target_reached == (len(expected) <= 9)
        assert session.usage.input_tokens == 11 * calls
        usage = [e for e in audit.events if isinstance(e, ModelUsageRecordedEvent)]
        assert len(usage) == calls
        if calls == 2:
            assert (
                summaries.requests[1].max_summary_tokens < summaries.requests[0].max_summary_tokens
            )


async def test_retained_content_over_target_skips_futile_retry_and_preserves_identity():
    summaries = RecordingSummary("useful")
    model = ScriptedModel(["old", "answer"])
    async with Agent(
        model=model,
        plugins=[
            Service(TokenCounter, Characters()),
            Compaction(
                trigger=lambda _: False, summarizer=summaries, keep_last_steps=1, target_tokens=20
            ),
        ],
    ).session() as session:
        await session.run("x" * 500)
        await session.run("retained " * 20)
        kept = session.messages[-2:]
        applied = await session.compact()
        assert applied is not None and applied.target_reached is False
        assert len(summaries.requests) == 1
        assert all(a is b for a, b in zip(kept, session.messages[-2:], strict=True))


@pytest.mark.parametrize(
    "texts,expected", [(("oversized", "ok"), "ok"), (("oversized", "still oversized"), None)]
)
async def test_explicit_summary_ceiling_retries_without_slicing_text(texts, expected):
    summaries = RecordingSummary(*texts)
    async with Agent(
        model=ScriptedModel(["old", "answer"]),
        plugins=[
            Service(TokenCounter, Characters()),
            Compaction(
                trigger=lambda _: False,
                summarizer=summaries,
                keep_last_steps=1,
                max_summary_tokens=4,
            ),
        ],
    ).session() as session:
        await session.run("x" * 500)
        await session.run("kept")
        original = session.messages
        finished = []
        session.on(CompactionFinishedEvent, finished.append)
        applied = await session.compact()
        assert (applied.summary if applied is not None else None) == expected
        assert len(summaries.requests) == 2
        assert session.usage.input_tokens == 22
        if expected is None:
            assert session.messages == original
            assert finished[0].after_tokens == finished[0].before_tokens
            assert finished[0].freed_tokens == 0


async def test_truncated_model_output_gets_one_larger_generation_attempt_and_no_partial_commit():
    llm = ScriptedModel(
        [
            ModelResponse(AssistantMessage("partial"), ModelUsage(10, 4), StopReason.MAX_TOKENS),
            ModelResponse(AssistantMessage("complete"), ModelUsage(12, 3)),
        ]
    )
    async with Agent(
        model=ScriptedModel(["old", "answer"]),
        plugins=[
            Compaction(trigger=lambda _: False, llm=llm, keep_last_steps=1, max_summary_tokens=16),
        ],
    ).session() as session:
        await session.run("x" * 4000)
        await session.run("kept")
        applied = await session.compact()
        assert applied is not None and applied.summary == "complete"
        assert llm.requests[1].settings.max_tokens > llm.requests[0].settings.max_tokens
        assert session.usage == ModelUsage(22, 7)


async def test_unknown_window_requires_finite_limit_but_absolute_target_works():
    summaries = RecordingSummary("done")
    model = ScriptedModel(["old", "kept"], info=ModelInfo("unknown"))
    async with Agent(
        model=model,
        plugins=[Compaction(trigger=lambda _: False, summarizer=summaries, keep_last_steps=1)],
    ).session() as session:
        await session.run("x" * 500)
        await session.run("kept")
        with pytest.raises(ConfigError, match="max_summary_tokens"):
            await session.compact()
        assert not summaries.requests
        handle = await session.mount(
            Compaction(
                trigger=lambda _: False, summarizer=summaries, keep_last_steps=1, target_tokens=200
            )
        )
        assert await session.compact() is not None
        await handle.aclose()


@pytest.mark.parametrize(
    "options",
    [
        {"summarizer": "model-name"},
        {"summarizer": RecordingSummary("a"), "llm": ScriptedModel([])},
        {"summarizer": RecordingSummary("a"), "prompt": "custom"},
        {"prompt": "  "},
        {"target_ratio": 0},
        {"target_ratio": float("inf")},
        {"target_ratio": True},
        {"target_ratio": 0.4, "target_tokens": 40},
        {"target_tokens": True},
        {"max_summary_tokens": 0},
        {"keep_last_steps": 0},
    ],
)
def test_configuration_rejects_contradictory_or_invalid_options(options):
    with pytest.raises(ValueError):
        Compaction(**options)


@pytest.mark.parametrize(
    "removed",
    ["focus", "keep_turns", "keep_steps", "keep_first_turns", "preserve_turns", "summary_tokens"],
)
def test_removed_constructor_arguments_have_no_aliases(removed):
    with pytest.raises(TypeError, match="unexpected keyword"):
        Compaction(**{removed: 1})


async def test_ratio_target_fails_with_unknown_window_before_provider_call():
    model = ScriptedModel([], info=ModelInfo("unknown"))
    with pytest.raises(ConfigError, match="context window"):
        await Agent(
            model=model, plugins=[Compaction(trigger=lambda _: False, target_ratio=0.5)]
        ).run("task")
    assert model.requests == []


async def test_calibration_and_request_snapshot_survive_start_observer_changes():
    counter = CalibratedTokenCounter()
    summaries = RecordingSummary("done")
    async with Agent(
        model=ScriptedModel(["old", "kept"]),
        plugins=[
            Service(TokenCounter, counter),
            Compaction(trigger=lambda _: False, summarizer=summaries, keep_last_steps=1),
        ],
    ).session() as session:
        await session.run("x" * 4000)
        await session.run("kept")

        def change(event):
            counter.ratio = 4.0

        session.on(CompactionStartedEvent, change)
        applied = await session.compact()
        assert applied is not None
        assert applied.before_tokens == summaries.requests[0].context.state.input_tokens
        assert applied.after_tokens < 100
        assert counter.ratio == 4.0


async def test_context_arriving_at_start_is_outside_the_attempt_snapshot():
    summaries = RecordingSummary("done")
    async with Agent(
        model=ScriptedModel(["old", "kept"]),
        plugins=[
            Service(TokenCounter, Characters()),
            Compaction(trigger=lambda _: False, summarizer=summaries, keep_last_steps=1),
        ],
    ).session() as session:
        await session.run("x" * 500)
        await session.run("kept")
        late = UserMessage("arrived after snapshot")

        async def append(event):
            await session.log.append(MessageAppendedEvent(late))

        session.on(CompactionStartedEvent, append)
        applied = await session.compact()
        assert applied is not None
        assert late in session.messages
        context = summaries.requests[0].context
        assert late not in (*context.history_to_compact, *context.retained_context)
        projected = tuple(message for message in session.messages if message is not late)
        assert applied.after_tokens == Characters().count_request(ModelRequest(projected))


async def test_model_output_is_recounted_with_the_session_tokenizer():
    llm = ScriptedModel(["too long", "ok"])
    async with Agent(
        model=ScriptedModel(["old", "kept"]),
        plugins=[
            Service(TokenCounter, Characters()),
            Compaction(trigger=lambda _: False, llm=llm, keep_last_steps=1, max_summary_tokens=4),
        ],
    ).session() as session:
        await session.run("x" * 500)
        await session.run("kept")
        applied = await session.compact()
        assert applied is not None and applied.summary == "ok"
        assert len(llm.requests) == 2
        assert llm.requests[1].settings.max_tokens < llm.requests[0].settings.max_tokens


async def test_small_ratio_target_rounds_down_without_inventing_a_minimum():
    summaries = RecordingSummary("done")
    model = ScriptedModel(["old", "kept"], info=ModelInfo("small", context_window=1000))
    async with Agent(
        model=model,
        plugins=[
            Compaction(
                trigger=lambda _: False,
                summarizer=summaries,
                keep_last_steps=1,
                target_ratio=0.0001,
            ),
        ],
    ).session() as session:
        await session.run("x" * 500)
        await session.run("kept")
        applied = await session.compact()
        assert applied is not None
        assert applied.target_tokens == 0 and applied.target_reached is False
        assert len(summaries.requests) == 1
