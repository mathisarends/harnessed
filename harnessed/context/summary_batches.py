from collections.abc import Awaitable, Callable
from dataclasses import replace

from harnessed.errors import ContextBudgetExceeded, ContextLengthExceeded
from harnessed.models import ModelRequest

from .budget import truncate
from .tokens import TokenCounter, count_request


class SummaryBatches:
    """Pack chronological transcript slices alongside a rolling handoff."""

    def __init__(
        self,
        transcript: str,
        render: Callable[[str, str], ModelRequest],
        *,
        counter: TokenCounter,
        window: int,
    ) -> None:
        self._transcript = transcript
        self._render = render
        self._counter = counter
        self._window = window
        self._position = 0
        self._provider_width: int | None = None

    @property
    def pending(self) -> bool:
        return self._position < len(self._transcript)

    async def output_allowance(self, requested: int) -> int:
        """Leave space for both the previous handoff and new history on each call."""
        base = self._render("", "")
        used = await count_request(self._counter, base)
        return min(requested, max(1, (self._window - used) // 3))

    async def summarize_next[T](
        self,
        previous: str,
        *,
        allowance: int,
        summarize: Callable[[ModelRequest], Awaitable[T]],
    ) -> T:
        """Advance only after a successful summary, shrinking on provider overflow."""
        base = self._render("", previous)
        used = await count_request(self._counter, base)
        room = self._window - allowance - used
        width = self._estimate_width(room)
        if self._provider_width is not None:
            width = min(width, self._provider_width)
        while True:
            end = self._position + width
            request = self._render(self._transcript[self._position : end], previous)
            used = await count_request(self._counter, request)
            if used + allowance <= self._window:
                request = replace(request, settings=replace(request.settings, max_tokens=allowance))
                try:
                    result = await summarize(request)
                except ContextLengthExceeded:
                    if width == 1:
                        raise
                    self._provider_width = width // 2
                else:
                    self._position = end
                    return result
            elif width == 1:
                raise ContextBudgetExceeded(used, allowance, self._window)
            width //= 2

    def _estimate_width(self, room: int) -> int:
        # Bound the local search to a nearby slice even for very large imports.
        width = max(1, room)
        remaining = len(self._transcript) - self._position
        while width < remaining:
            fragment = self._transcript[self._position : self._position + width]
            if self._counter.count_text(fragment) >= room:
                break
            width *= 2
        fragment = self._transcript[self._position : self._position + width]
        return max(1, len(truncate(fragment, room, self._counter)))
