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
    preserve_inputs holds event offsets of active-turn inputs kept verbatim.
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
    before_tokens: int | None = None
    after_tokens: int | None = None
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
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in (self.before_tokens, self.after_tokens)
        ):
            raise ValueError("Compaction token counts must be non-negative integers")


@dataclass(frozen=True, slots=True)
class CompactionStartedEvent(TransientEvent):
    """Start a summarization attempt, paired with CompactionFinishedEvent."""

    type: Literal[EventKind.COMPACTION_STARTED] = field(
        default=EventKind.COMPACTION_STARTED, kw_only=True
    )
    compaction_id: str


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", CompactionOutcome(self.outcome))
