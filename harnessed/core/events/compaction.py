from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from harnessed.core.events.base import DurableEvent, TransientEvent
from harnessed.core.events.kinds import EventKind


class CompactionOutcome(StrEnum):
    APPLIED = "applied"
    DISCARDED = "discarded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CompactionAppliedEvent(DurableEvent):
    """Replace a history prefix at a closed turn or completed step boundary.

    preserve_turns pins full turns; retained_turns is temporary sliding retention.
    preserve_inputs holds event offsets of task/context inputs kept verbatim.
    Occupancy ratios measure the whole input request, excluding output reserve.
    This is an applied history change, not a notification for every attempted
    summary; failed or non-reducing summaries do not produce this record.
    """

    __hash__ = None

    type: Literal[EventKind.COMPACTION_APPLIED] = field(
        default=EventKind.COMPACTION_APPLIED, kw_only=True
    )
    through: int
    summary: str
    preserve_turns: tuple[str, ...] = ()
    policy: str = ""
    before_tokens: int = field(kw_only=True)
    after_tokens: int = field(kw_only=True)
    context_window: int | None = None
    reserve_tokens: int = 0
    target_tokens: int | None = None
    id: str | None = None
    preserve_inputs: tuple[int, ...] = ()
    retained_turns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.id is not None and (not isinstance(self.id, str) or not self.id):
            raise ValueError("Compaction ids must be non-empty strings or None")
        turns = tuple(self.preserve_turns)
        if any(not isinstance(turn, str) or not turn for turn in turns) or len(set(turns)) != len(
            turns
        ):
            raise ValueError("Preserved turn ids must be unique non-empty strings")
        object.__setattr__(self, "preserve_turns", turns)
        inputs = tuple(self.preserve_inputs)
        if any(type(offset) is not int or offset < 0 for offset in inputs) or len(
            set(inputs)
        ) != len(inputs):
            raise ValueError("Preserved input offsets must be unique non-negative integers")
        object.__setattr__(self, "preserve_inputs", inputs)
        retained = tuple(self.retained_turns)
        if any(not isinstance(turn, str) or not turn for turn in retained) or len(
            set(retained)
        ) != len(retained):
            raise ValueError("Retained turn ids must be unique non-empty strings")
        object.__setattr__(self, "retained_turns", retained)
        _validate_metrics(
            self.before_tokens, self.context_window, self.reserve_tokens, self.target_tokens
        )
        if type(self.after_tokens) is not int or self.after_tokens < 0:
            raise ValueError("Compaction token counts must be non-negative integers")

    @property
    def before_ratio(self) -> float | None:
        return self.before_tokens / self.context_window if self.context_window is not None else None

    @property
    def after_ratio(self) -> float | None:
        if self.context_window is None:
            return None
        return self.after_tokens / self.context_window

    @property
    def freed_tokens(self) -> int:
        return self.before_tokens - self.after_tokens

    @property
    def target_reached(self) -> bool | None:
        if self.target_tokens is None:
            return None
        return self.after_tokens <= self.target_tokens


@dataclass(frozen=True, slots=True)
class CompactionStartedEvent(TransientEvent):
    """Start a summarization attempt, paired with CompactionFinishedEvent."""

    type: Literal[EventKind.COMPACTION_STARTED] = field(
        default=EventKind.COMPACTION_STARTED, kw_only=True
    )
    compaction_id: str
    before_tokens: int = field(kw_only=True)
    context_window: int | None = None
    reserve_tokens: int = 0
    target_tokens: int | None = None

    def __post_init__(self) -> None:
        _validate_metrics(
            self.before_tokens, self.context_window, self.reserve_tokens, self.target_tokens
        )

    @property
    def before_ratio(self) -> float | None:
        return self.before_tokens / self.context_window if self.context_window is not None else None


@dataclass(frozen=True, slots=True)
class CompactionFinishedEvent(TransientEvent):
    """Close a compaction attempt, including discarded or failed summaries.

    A successful CompactionAppliedEvent with the same id precedes this event.
    """

    type: Literal[EventKind.COMPACTION_FINISHED] = field(
        default=EventKind.COMPACTION_FINISHED, kw_only=True
    )
    compaction_id: str
    outcome: CompactionOutcome
    before_tokens: int = field(kw_only=True)
    context_window: int | None = None
    reserve_tokens: int = 0
    target_tokens: int | None = None
    after_tokens: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", CompactionOutcome(self.outcome))
        _validate_metrics(
            self.before_tokens, self.context_window, self.reserve_tokens, self.target_tokens
        )
        if self.after_tokens is not None and (
            type(self.after_tokens) is not int or self.after_tokens < 0
        ):
            raise ValueError("Compaction token counts must be non-negative integers")

    @property
    def before_ratio(self) -> float | None:
        return self.before_tokens / self.context_window if self.context_window is not None else None

    @property
    def after_ratio(self) -> float | None:
        if self.after_tokens is None or self.context_window is None:
            return None
        return self.after_tokens / self.context_window

    @property
    def freed_tokens(self) -> int | None:
        return self.before_tokens - self.after_tokens if self.after_tokens is not None else None

    @property
    def target_reached(self) -> bool | None:
        if self.after_tokens is None or self.target_tokens is None:
            return None
        return self.after_tokens <= self.target_tokens


def _validate_metrics(before: int, window: int | None, reserve: int, target: int | None) -> None:
    if any(type(value) is not int or value < 0 for value in (before, reserve)):
        raise ValueError("Compaction token counts must be non-negative integers")
    if window is not None and (type(window) is not int or window < 1):
        raise ValueError("Compaction windows must be positive integers")
    if target is not None and (type(target) is not int or target < 0):
        raise ValueError("Resolved compaction targets must be non-negative integers")
