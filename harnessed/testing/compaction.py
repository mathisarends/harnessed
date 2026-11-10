import json

from harnessed.core.budget import truncate
from harnessed.core.compaction import CompactionRequest, Summary
from harnessed.core.messages import to_dict
from harnessed.core.tokens import ApproximateTokenCounter, TokenCounter


class ExtractiveSummarizer:
    """Offline fallback: budgeted message excerpts, without an extra model request."""

    def __init__(self, counter: TokenCounter | None = None) -> None:
        self.counter = counter if counter is not None else ApproximateTokenCounter()

    async def summarize(self, request: CompactionRequest) -> Summary:
        messages = request.context.history_to_compact
        max_tokens = request.max_summary_tokens
        if not messages:
            return Summary("No retained conversation content.")
        allowance = max(1, max_tokens // len(messages) - 8)
        excerpts = []
        for message in messages:
            body = message.text
            if message.tool_calls:
                body += "\nTool calls: " + json.dumps(
                    to_dict(message.tool_calls), ensure_ascii=False
                )
            if self.counter.count_text(body) > allowance:
                head = truncate(body, allowance // 2, self.counter)
                tail = truncate(body[::-1], allowance // 2, self.counter)[::-1]
                body = f"{head}\n[...]\n{tail}"
            excerpts.append(f"{message.role}: {body}")
        return Summary(truncate("\n\n".join(excerpts), max_tokens, self.counter))
