from itertools import pairwise
from typing import Annotated

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from .checkpoint import CheckpointEntry, CheckpointExtension, SessionCheckpoint
from .codec import _validate_event

_CONFIG = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


@dataclass(frozen=True, config=_CONFIG)
class SessionChange:
    """One new durable event at its conversation-local, zero-based log position."""

    __hash__ = None

    sequence: Annotated[int, Field(ge=0)]
    event: CheckpointEntry

    def __post_init__(self) -> None:
        if not isinstance(self.event, CheckpointExtension):
            _validate_event(self.event)


@dataclass(frozen=True, config=_CONFIG)
class SessionCommit:
    """A checkpoint and the ordered changes to apply in the same transaction.

    Runtime commits contain every new durable event since the last acknowledged
    save. Restoration does not re-emit historical changes. A manually constructed
    checkpoint-only commit can seed application-owned storage without importing
    a historical event archive. Neither value dictates the backend's schema.
    """

    __hash__ = None

    checkpoint: SessionCheckpoint
    changes: tuple[SessionChange, ...] = ()

    def __post_init__(self) -> None:
        if any(
            following.sequence != previous.sequence + 1
            for previous, following in pairwise(self.changes)
        ):
            raise ValueError("Session changes must have contiguous ascending sequences")
        if self.changes and self.changes[-1].sequence + 1 != self.next_sequence:
            raise ValueError("Session changes must end at their checkpoint's next sequence")

    @property
    def next_sequence(self) -> int:
        """The first log position not represented by this checkpoint."""
        return self.checkpoint.offset + len(self.checkpoint.events)


__all__ = ["SessionChange", "SessionCommit"]
