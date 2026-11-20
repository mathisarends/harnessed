from typing import NamedTuple

from harnessed.kernel import Emit
from harnessed.models import ModelRequest

from .tokens import TokenCounter


class _CompactionNote(NamedTuple):
    text: str
    min_summary_tokens: int


class FittedNotes(NamedTuple):
    prefix: str
    keys: tuple[str, ...]
    summary_tokens: int


class CompactionNotes:
    def __init__(self) -> None:
        self._notes: dict[str, _CompactionNote] = {}

    def add(self, *, key: str, text: str, min_summary_tokens: int) -> None:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Compaction note keys must be non-empty strings")
        if key in self._notes:
            raise ValueError(f"Duplicate compaction note key: {key!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Compaction notes must contain text")
        if type(min_summary_tokens) is not int or min_summary_tokens < 1:
            raise ValueError("Compaction notes must leave a positive summary token budget")
        self._notes[key] = _CompactionNote(text.strip(), min_summary_tokens)

    def fit(self, counter: TokenCounter, ceiling: int) -> FittedNotes:
        keys: list[str] = []
        prefix = ""
        remaining = ceiling
        minimum = 1
        for key, note in self._notes.items():
            proposed = f"{prefix}{note.text}\n\n"
            available = ceiling - counter.count_text(proposed)
            required = max(minimum, note.min_summary_tokens)
            if available < required:
                continue
            keys.append(key)
            prefix = proposed
            remaining = available
            minimum = required
        return FittedNotes(prefix, tuple(keys), remaining)


class PrepareCompaction(Emit):
    """Collect optional handover notes before generating compaction candidates.

    The request is the effective agent request whose history will be compacted.
    Hooks run once per compaction in priority and registration order. Notes may
    be skipped on any candidate. Observe CompactionAppliedEvent to learn which
    keys were committed.
    """

    def __init__(self, request: ModelRequest, *, notes: CompactionNotes) -> None:
        self._request = request
        self._notes = notes

    @property
    def request(self) -> ModelRequest:
        return self._request

    def add_note(self, *, key: str, text: str, min_summary_tokens: int = 1) -> None:
        """Offer a prefix with a unique key and a minimum budget for generated text."""
        self._notes.add(key=key, text=text, min_summary_tokens=min_summary_tokens)
