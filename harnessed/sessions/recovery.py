import json
from collections.abc import Iterable, Mapping
from functools import cached_property
from typing import Annotated, Literal, Self

from pydantic import TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from harnessed.errors import SessionFormatError
from harnessed.events import TurnEndedEvent, TurnStartedEvent
from harnessed.models.assets import AssetValue
from harnessed.models.values import JSONValue

from .files import TextValue
from .formats import RECOVERY
from .journal import (
    STORAGE_CONFIG,
    JournalEntry,
    JournalEvent,
    JournalRequest,
    MessageReferences,
    SessionSnapshot,
)
from .state import SessionState
from .upcasting import integer_version


@dataclass(frozen=True, config=STORAGE_CONFIG)
class SessionRecovery:
    """A stored session: a snapshot plus the journal entries written after it.

    A ``SessionStore`` reopens a session from its stored documents through
    this value; ``session.recovery()`` captures it on demand, and
    ``Agent.session(state=...)`` accepts it as an in-memory value. Reopening
    restores the snapshot and replays the journal, so an open turn continues
    where it stopped. ``journal`` starts at the snapshot's ``sequence`` and has
    no gaps. It may hold idle events such as queued input or control changes,
    and at most one open turn; a closed turn always ends at a newer snapshot.
    """

    __hash__ = None

    snapshot: SessionSnapshot
    journal: tuple[JournalEntry, ...] = ()
    version: Annotated[Literal[6], integer_version] = 6

    def __post_init__(self) -> None:
        sequences = [entry.sequence for entry in self.journal]
        start = self.snapshot.sequence
        if sequences != list(range(start, start + len(sequences))):
            raise ValueError("A journal must continue its snapshot without gaps")
        _check_open_turn(self.events)

    @classmethod
    def from_journal(cls, snapshot: SessionSnapshot, entries: Iterable[JournalEntry]) -> Self:
        """Combine a stored snapshot with the stored entries, in sequence order.

        Entries before the snapshot are skipped, so a store may keep them as an
        audit trail instead of deleting them when a new snapshot arrives.
        """
        journal = tuple(entry for entry in entries if entry.sequence >= snapshot.sequence)
        try:
            return cls(snapshot, journal)
        except (ValidationError, ValueError, TypeError) as error:
            raise SessionFormatError("The journal does not continue its snapshot") from error

    @property
    def session_id(self) -> str:
        """The session this value reopens."""
        return self.snapshot.session_id

    @property
    def state(self) -> SessionState:
        """The conversation at the snapshot; an open turn is not part of it yet."""
        return self.snapshot.state

    @cached_property
    def events(self) -> tuple[JournalEvent, ...]:
        """The journal's events after the snapshot, with every request rebuilt in full."""
        # Mirror the encoder: references restart from the snapshot when the turn starts.
        references = MessageReferences()
        events: list[JournalEvent] = []
        for entry in self.journal:
            record = entry.record
            if isinstance(record, TurnStartedEvent):
                references = MessageReferences(self.snapshot.state.history)
            if isinstance(record, JournalRequest):
                events.append(references.resolve(record))
            else:
                events.append(record)
        return tuple(events)

    @property
    def assets(self) -> tuple[AssetValue, ...]:
        """Images the journal needs beyond the conversation state."""
        values = {value.ref.sha256: value for value in self.snapshot.assets}
        for entry in self.journal:
            values.update((value.ref.sha256, value) for value in entry.assets)
        return tuple(values.values())

    @property
    def files(self) -> tuple[TextValue, ...]:
        """Text the journal needs beyond the conversation state, newest per key."""
        values = {value.key: value for value in self.snapshot.files}
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
        return cls.from_json(RECOVERY.encode_input(payload))

    @classmethod
    def from_json(cls, payload: str | bytes) -> Self:
        """Validate structural data without needing application event classes."""
        return RECOVERY.decode(TypeAdapter(cls), payload)


def _check_open_turn(events: tuple[JournalEvent, ...]) -> None:
    """Allow idle events followed by at most one turn that is still open."""
    starts = sum(isinstance(event, TurnStartedEvent) for event in events)
    if starts > 1 or any(isinstance(event, TurnEndedEvent) for event in events):
        raise ValueError("A journal after its snapshot can hold only one open turn")


_CHECKPOINT = TypeAdapter(SessionRecovery)
