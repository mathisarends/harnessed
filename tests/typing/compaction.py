from typing import assert_type

from harnessed.events import (
    CompactionAppliedEvent,
    CompactionFinishedEvent,
    CompactionStartedEvent,
)
from harnessed.models import Message, SystemMessage
from harnessed.plugins import (
    DEFAULT_COMPACTION_PROMPT,
    Compaction,
    CompactionPrompt,
    ContextState,
)
from harnessed.services import CompactionContext, CompactionRequest, Summarizer, Summary


class CustomSummarizer:
    async def summarize(self, request: CompactionRequest) -> Summary:
        assert_type(request.context.history_to_compact, tuple[Message, ...])
        assert_type(request.context.retained_context, tuple[Message, ...])
        assert_type(request.context.instructions, tuple[SystemMessage, ...])
        assert_type(request.context.state, ContextState)
        assert_type(request.context.target_tokens, int | None)
        assert_type(request.max_summary_tokens, int)
        return Summary("Continue the task.")


async def prompt(context: CompactionContext) -> str:
    return DEFAULT_COMPACTION_PROMPT + f"\nTarget: {context.target_tokens}"


factory: CompactionPrompt = prompt
summarizer: Summarizer = CustomSummarizer()
configured = Compaction(prompt=factory, keep_last_steps=3, target_ratio=0.4)
custom = Compaction(summarizer=summarizer, max_summary_tokens=2000)


def metrics(
    started: CompactionStartedEvent,
    applied: CompactionAppliedEvent,
    finished: CompactionFinishedEvent,
) -> None:
    assert_type(started.before_tokens, int)
    assert_type(started.before_ratio, float | None)
    assert_type(applied.after_tokens, int)
    assert_type(applied.freed_tokens, int)
    assert_type(applied.target_reached, bool | None)
    assert_type(finished.after_tokens, int | None)
    assert_type(finished.after_ratio, float | None)
    assert_type(finished.freed_tokens, int | None)
