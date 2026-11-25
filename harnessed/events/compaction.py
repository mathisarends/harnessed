from dataclasses import field
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, NonNegativeInt, PositiveInt
from pydantic.dataclasses import dataclass

from harnessed.models.values import VALUE_CONFIG, unique

from .base import DurableEvent, Identifier, TransientEvent
from .kinds import EventKind

type _UniqueIdentifiers = Annotated[tuple[Identifier, ...], AfterValidator(unique)]
type _NoteKey = Annotated[str, Field(pattern=r"\S")]


class CompactionOutcome(StrEnum):
    APPLIED = "applied"
    DISCARDED = "discarded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, config=VALUE_CONFIG)
class CompactionAppliedEvent(DurableEvent):
    """Replace a prefix ending at imported history, a closed turn or a completed step.

    preserve_turns pins full turns; retained_turns is temporary sliding retention.
    preserve_inputs holds event offsets of task/context inputs kept verbatim.
    Occupancy ratios measure the whole input request, excluding output reserve.
    accepted_note_keys identifies the plugin notes included in the summary.
    This is an applied history change, not a notification for every attempted
    summary; failed or non-reducing summaries do not produce this record.
    """

    __hash__ = None

    type: Literal[EventKind.COMPACTION_APPLIED] = field(
        default=EventKind.COMPACTION_APPLIED, kw_only=True
    )
    through: int
    summary: str
    preserve_turns: _UniqueIdentifiers = ()
    policy: str = ""
    before_tokens: NonNegativeInt = field(kw_only=True)
    after_tokens: NonNegativeInt = field(kw_only=True)
    context_window: PositiveInt | None = None
    reserve_tokens: NonNegativeInt = 0
    target_tokens: NonNegativeInt | None = None
    id: Identifier | None = None
    preserve_inputs: Annotated[tuple[NonNegativeInt, ...], AfterValidator(unique)] = ()
    retained_turns: _UniqueIdentifiers = ()
    accepted_note_keys: Annotated[tuple[_NoteKey, ...], AfterValidator(unique)] = field(
        default=(), kw_only=True
    )

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


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class CompactionStartedEvent(TransientEvent):
    """Start a summarization attempt, paired with CompactionFinishedEvent."""

    type: Literal[EventKind.COMPACTION_STARTED] = field(
        default=EventKind.COMPACTION_STARTED, kw_only=True
    )
    compaction_id: str
    before_tokens: NonNegativeInt = field(kw_only=True)
    context_window: PositiveInt | None = None
    reserve_tokens: NonNegativeInt = 0
    target_tokens: NonNegativeInt | None = None

    @property
    def before_ratio(self) -> float | None:
        return self.before_tokens / self.context_window if self.context_window is not None else None


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class CompactionFinishedEvent(TransientEvent):
    """Close a compaction attempt, including discarded or failed summaries.

    A successful CompactionAppliedEvent with the same id precedes this event.
    """

    type: Literal[EventKind.COMPACTION_FINISHED] = field(
        default=EventKind.COMPACTION_FINISHED, kw_only=True
    )
    compaction_id: str
    outcome: CompactionOutcome
    before_tokens: NonNegativeInt = field(kw_only=True)
    context_window: PositiveInt | None = None
    reserve_tokens: NonNegativeInt = 0
    target_tokens: NonNegativeInt | None = None
    after_tokens: NonNegativeInt | None = None

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
