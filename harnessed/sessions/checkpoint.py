import json
from collections.abc import Iterable, Mapping
from functools import cached_property
from typing import Literal, Self

from pydantic import TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from harnessed.errors import SessionFormatError
from harnessed.events import TurnEndedEvent, TurnStartedEvent
from harnessed.models.assets import AssetValue
from harnessed.models.values import JSONValue

from .commit import SessionCommit
from .files import TextValue
from .journal import (
    STORAGE_CONFIG,
    CheckpointEntry,
    CheckpointRequest,
    JournalEntry,
    MessageReferences,
    SessionBoundary,
    encode_document,
)
from .state import SessionState


@dataclass(frozen=True, config=STORAGE_CONFIG)
class SessionCheckpoint:
    """A stored session: a boundary plus the journal entries written after it.

    Pass a checkpoint to ``Agent.session(state=...)`` to reopen the session under
    its ID. Reopening restores the boundary and replays the journal, so an open
    turn continues where it stopped. ``journal`` starts at the boundary's
    ``sequence`` and has no gaps. It may hold idle events such as queued input
    or control changes, and at most one open turn; a closed turn always ends at
    a newer boundary.

    Applications usually keep the boundary and the entries apart and combine
    them with ``from_journal``. A store that keeps one document instead folds
    each commit in with ``from_commit`` and saves ``to_dict()``.
    """

    __hash__ = None

    boundary: SessionBoundary
    journal: tuple[JournalEntry, ...] = ()
    version: Literal[6] = 6

    def __post_init__(self) -> None:
        sequences = [entry.sequence for entry in self.journal]
        start = self.boundary.sequence
        if sequences != list(range(start, start + len(sequences))):
            raise ValueError("A journal must continue its boundary without gaps")
        _check_open_turn(self.events)

    @classmethod
    def from_journal(cls, boundary: SessionBoundary, entries: Iterable[JournalEntry]) -> Self:
        """Combine a stored boundary with the stored entries, in sequence order.

        Entries before the boundary are skipped, so a store may keep them as an
        audit trail instead of deleting them when a new boundary arrives.
        """
        journal = tuple(entry for entry in entries if entry.sequence >= boundary.sequence)
        try:
            return cls(boundary, journal)
        except (ValidationError, ValueError, TypeError) as error:
            raise SessionFormatError("The journal does not continue its boundary") from error

    @classmethod
    def from_commit(cls, commit: SessionCommit, previous: Self | None = None) -> Self:
        """The checkpoint a store holds after ``commit``.

        ``previous`` is the checkpoint stored before this commit; the session's
        first commit needs none because it always carries a boundary. A store
        that keeps one document per session saves the result's ``to_dict()``.
        """
        if previous is not None and previous.session_id != commit.session_id:
            raise SessionFormatError("A commit can only extend its own session's checkpoint")
        boundary = commit.boundary
        if boundary is None:
            if previous is None:
                raise SessionFormatError("The first commit of a session carries its boundary")
            boundary = previous.boundary
        entries = (*previous.journal, *commit.journal) if previous is not None else commit.journal
        return cls.from_journal(boundary, entries)

    @property
    def session_id(self) -> str:
        """The session this checkpoint reopens."""
        return self.boundary.session_id

    @property
    def state(self) -> SessionState:
        """The conversation at the boundary; an open turn is not part of it yet."""
        return self.boundary.state

    @cached_property
    def events(self) -> tuple[CheckpointEntry, ...]:
        """The journal's events after the boundary, with every request rebuilt in full."""
        # Mirror the encoder: references restart from the boundary when the turn starts.
        references = MessageReferences()
        events: list[CheckpointEntry] = []
        for entry in self.journal:
            record = entry.record
            if isinstance(record, TurnStartedEvent):
                references = MessageReferences(self.boundary.state.history)
            if isinstance(record, CheckpointRequest):
                events.append(references.resolve(record))
            else:
                events.append(record)
        return tuple(events)

    @property
    def assets(self) -> tuple[AssetValue, ...]:
        """Images the journal needs beyond the conversation state."""
        values = {value.ref.sha256: value for value in self.boundary.assets}
        for entry in self.journal:
            values.update((value.ref.sha256, value) for value in entry.assets)
        return tuple(values.values())

    @property
    def files(self) -> tuple[TextValue, ...]:
        """Text the journal needs beyond the conversation state, newest per key."""
        values = {value.key: value for value in self.boundary.files}
        for entry in self.journal:
            values.update((value.key, value) for value in entry.files)
        return tuple(values.values())

    def to_json(self) -> str:
        """Serialize the canonical versioned document in memory, without I/O."""
        return _CHECKPOINT.dump_json(self).decode("utf-8")

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the canonical JSON document as Python values, e.g. for a JSONB column."""
        return json.loads(self.to_json())

    @classmethod
    def from_dict(cls, payload: Mapping[str, JSONValue]) -> Self:
        """Validate a document returned by ``to_dict``."""
        return cls.from_json(encode_document(payload, "Invalid session checkpoint"))

    @classmethod
    def from_json(cls, payload: str | bytes) -> Self:
        """Validate structural data without needing application event classes."""
        try:
            return TypeAdapter(cls).validate_json(payload)
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise SessionFormatError("Invalid session checkpoint") from error


def _check_open_turn(events: tuple[CheckpointEntry, ...]) -> None:
    """Allow idle events followed by at most one turn that is still open."""
    starts = sum(isinstance(event, TurnStartedEvent) for event in events)
    if starts > 1 or any(isinstance(event, TurnEndedEvent) for event in events):
        raise ValueError("A journal after its boundary can hold only one open turn")


_CHECKPOINT = TypeAdapter(SessionCheckpoint)
