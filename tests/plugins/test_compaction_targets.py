import pytest

from harnessed import Agent, AgentLoop, Compaction, Context, Plugin, tool
from harnessed.context import CompactionRequest, Summary
from harnessed.errors import ConfigError
from harnessed.events import (
    CompactionAppliedEvent,
    CompactionFinishedEvent,
    CompactionStartedEvent,
    MessageAppendedEvent,
    ModelUsageRecordedEvent,
)
from harnessed.hooks import PrepareCompaction
from harnessed.kernel import hook
from harnessed.models import (
    AssistantMessage,
    ModelInfo,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    StopReason,
    UserMessage,
)
from harnessed.plugins import FinalStepPolicy
from harnessed.runtime.budget import ContextBudget
from harnessed.runtime.plugin import Service
from harnessed.runtime.tokens import CalibratedTokenCounter, TokenCounter
from harnessed.sessions import SessionState
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


_NOTE = 'Read the checkpoint with read_file(path="task.md").'
_NOTE_KEY = "test.task-memory"


class TaskMemory(Plugin):
    @tool(readonly=True)
    def read_file(self, path: str) -> str:
        return f"Checkpoint in {path}"

    @hook(PrepareCompaction)
    def _handover(self, event: PrepareCompaction) -> None:
        if any(tool.name == "read_file" for tool in event.request.tools):
            event.add_note(key=_NOTE_KEY, text=_NOTE, min_summary_tokens=10)


@pytest.mark.parametrize("tight_budget,final_step", [(False, False), (True, False), (False, True)])
async def test_automatic_compaction_carries_fitting_notes_into_first_request_and_reopen(
    tight_budget: bool, final_step: bool
):
    include_note = not tight_budget and not final_step
    note_cost = len(_NOTE) + 2
    ceiling = note_cost + (9 if tight_budget else 10)
    summary = RecordingSummary("continue")
    model = ScriptedModel(["old", "retained", "answer"])
    agent = Agent(
        model=model,
        loop=AgentLoop(max_steps=1, final_step=FinalStepPolicy.SUMMARIZE) if final_step else None,
        plugins=[TaskMemory(), Service(TokenCounter, Characters())],
        context=Context(
            compaction=Compaction(
                trigger=lambda state: state.turns_since_compaction >= 2,
                summarizer=summary,
                keep_last_steps=1,
                max_summary_tokens=ceiling,
            )
        ),
    )
    async with agent.session() as session:
        await session.run("x" * 500)
        await session.run("keep")
        audit = Audit(session)
        await session.run("next")
        applied = next(event for event in audit.events if isinstance(event, CompactionAppliedEvent))
        expected = f"{_NOTE}\n\ncontinue" if include_note else "continue"
        assert applied.summary == expected
        assert applied.accepted_note_keys == ((_NOTE_KEY,) if include_note else ())
        assert summary.requests[0].max_summary_tokens == (10 if include_note else ceiling)
        projected = f"Conversation summary:\n{expected}"
        assert any(message.text == projected for message in model.requests[2].messages)
        state = SessionState.from_json(session.snapshot().to_json())

    async with agent.session(state=state) as reopened:
        assert any(message.text == projected for message in reopened.messages)


async def test_note_selection_is_recomputed_for_a_smaller_candidate():
    ceiling = len(_NOTE) + 2 + 10
    summary = RecordingSummary("x" * (ceiling + 1), "ok")
    async with Agent(
        model=ScriptedModel(["old", "kept"]),
        plugins=[TaskMemory(), Service(TokenCounter, Characters())],
        context=Context(
            compaction=Compaction(
                trigger=lambda _: False,
                summarizer=summary,
                keep_last_steps=1,
                max_summary_tokens=ceiling,
            )
        ),
    ).session() as session:
        await session.run("x" * 500)
        await session.run("keep")
        preparations: list[PrepareCompaction] = []
        session.on(PrepareCompaction, preparations.append)
        applied = await session.compact()
        assert applied is not None and applied.summary == "ok"
        assert applied.accepted_note_keys == ()
        assert len(preparations) == 1
        assert [request.max_summary_tokens for request in summary.requests] == [10, ceiling - 1]


async def test_notes_are_omitted_when_the_measured_request_misses_the_target():
    class NoteCounter(Characters):
        def count_request(self, request: ModelRequest) -> int:
            extra = 200 if any(_NOTE in message.text for message in request.messages) else 0
            return super().count_request(request) + extra

    summary = RecordingSummary("ok")
    async with Agent(
        model=ScriptedModel(["old", "kept"]),
        plugins=[TaskMemory(), Service(TokenCounter, NoteCounter())],
        context=Context(
            compaction=Compaction(
                trigger=lambda _: False,
                summarizer=summary,
                keep_last_steps=1,
                target_tokens=150,
            )
        ),
    ).session() as session:
        await session.run("x" * 500)
        await session.run("keep")
        applied = await session.compact()
        assert applied is not None and applied.summary == "ok"
        assert applied.accepted_note_keys == () and applied.target_reached
        assert len(summary.requests) == 1


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
        ],
        context=Context(
            compaction=Compaction(
                trigger=lambda _: False, summarizer=summaries, keep_last_steps=1, **target
            )
        ),
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
        plugins=[Service(TokenCounter, Characters())],
        context=Context(
            compaction=Compaction(
                trigger=lambda _: False, summarizer=summaries, keep_last_steps=1, target_tokens=76
            )
        ),
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
        plugins=[Service(TokenCounter, Characters())],
        context=Context(
            compaction=Compaction(
                trigger=lambda _: False, summarizer=summaries, keep_last_steps=1, target_tokens=20
            )
        ),
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
        plugins=[Service(TokenCounter, Characters())],
        context=Context(
            compaction=Compaction(
                trigger=lambda _: False,
                summarizer=summaries,
                keep_last_steps=1,
                max_summary_tokens=4,
            )
        ),
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


@pytest.mark.parametrize("with_note", [False, True])
async def test_truncated_model_output_gets_one_larger_generation_attempt_and_no_partial_commit(
    with_note: bool,
):
    llm = ScriptedModel(
        [
            ModelResponse(AssistantMessage("partial"), ModelUsage(10, 4), StopReason.MAX_TOKENS),
            ModelResponse(AssistantMessage("complete"), ModelUsage(12, 3)),
        ]
    )
    ceiling = 16
    plugins: list[Plugin] = [Service(TokenCounter, Characters())]
    if with_note:
        plugins.append(TaskMemory())
        ceiling += len(_NOTE) + 2
    async with Agent(
        model=ScriptedModel(["old", "answer"]),
        plugins=plugins,
        context=Context(
            compaction=Compaction(
                trigger=lambda _: False, llm=llm, keep_last_steps=1, max_summary_tokens=ceiling
            )
        ),
    ).session() as session:
        await session.run("x" * 4000)
        await session.run("kept")
        applied = await session.compact()
        expected = f"{_NOTE}\n\ncomplete" if with_note else "complete"
        assert applied is not None and applied.summary == expected
        assert applied.accepted_note_keys == ((_NOTE_KEY,) if with_note else ())
        assert [request.settings.max_tokens for request in llm.requests] == [16, 32]
        assert session.usage == ModelUsage(22, 7)


async def test_unknown_window_requires_finite_limit_but_absolute_target_works():
    summaries = RecordingSummary("done")
    model = ScriptedModel(["old", "kept"], info=ModelInfo("unknown"))
    async with Agent(
        model=model,
        context=Context(
            compaction=Compaction(trigger=lambda _: False, summarizer=summaries, keep_last_steps=1)
        ),
    ).session() as session:
        await session.run("x" * 500)
        await session.run("kept")
        with pytest.raises(ConfigError, match="max_summary_tokens"):
            await session.compact()
        assert not summaries.requests
        state = session.snapshot()
    policy = Compaction(
        trigger=lambda _: False, summarizer=summaries, keep_last_steps=1, target_tokens=200
    )
    async with Agent(model=model, context=Context(compaction=policy)).session(
        state=state
    ) as session:
        assert await session.compact() is not None


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
            model=model,
            context=Context(compaction=Compaction(trigger=lambda _: False, target_ratio=0.5)),
        ).run("task")
    assert model.requests == []


async def test_calibration_and_request_snapshot_survive_start_observer_changes():
    counter = CalibratedTokenCounter()
    summaries = RecordingSummary("done")
    async with Agent(
        model=ScriptedModel(["old", "kept"]),
        plugins=[Service(TokenCounter, counter)],
        context=Context(
            compaction=Compaction(trigger=lambda _: False, summarizer=summaries, keep_last_steps=1)
        ),
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
        plugins=[Service(TokenCounter, Characters())],
        context=Context(
            compaction=Compaction(trigger=lambda _: False, summarizer=summaries, keep_last_steps=1)
        ),
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
        plugins=[Service(TokenCounter, Characters())],
        context=Context(
            compaction=Compaction(
                trigger=lambda _: False, llm=llm, keep_last_steps=1, max_summary_tokens=4
            )
        ),
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
        context=Context(
            compaction=Compaction(
                trigger=lambda _: False,
                summarizer=summaries,
                keep_last_steps=1,
                target_ratio=0.0001,
            )
        ),
    ).session() as session:
        await session.run("x" * 500)
        await session.run("kept")
        applied = await session.compact()
        assert applied is not None
        assert applied.target_tokens == 0 and applied.target_reached is False
        assert len(summaries.requests) == 1
