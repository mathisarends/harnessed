"""The storage contract between a session and the application that persists it.

The application implements two operations, ``read`` and ``write``, against its
own database or files. Harnessed owns everything else: when to write, what a
commit contains, how stored documents are decoded and how a session reopens
from them. Harnessed itself never stores anything.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from harnessed.models.values import JSONValue

from .checkpoint import SessionCheckpoint
from .commit import SessionCommit
from .journal import JournalEntry, SessionBoundary

type StoredDocument = Mapping[str, JSONValue] | str | bytes
"""A stored boundary or journal entry: a JSON document as a dict, text or bytes."""


@dataclass(frozen=True)
class StoredSession:
    """What a store holds for one session: its boundary and the journal after it.

    ``journal`` lists the stored entries in sequence order. It may still contain
    entries from before the boundary; they are skipped when the session reopens.
    """

    boundary: StoredDocument
    journal: Sequence[StoredDocument] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "journal", tuple(self.journal))

    def checkpoint(self) -> SessionCheckpoint:
        """Decode the documents into the checkpoint the session reopens from."""
        boundary = _decode_boundary(self.boundary)
        entries = [_decode_entry(document) for document in self.journal]
        return SessionCheckpoint.from_journal(boundary, entries)


@runtime_checkable
class SessionStore(Protocol):
    """Application-owned persistence for sessions, passed as ``agent.session(store=...)``.

    A session opened with an ``id`` reads its stored documents once and reopens
    from them. Afterwards it calls ``write`` at every durability barrier and
    waits for it to return. Child sessions write through their parent's store.
    """

    async def read(self, session_id: str) -> StoredSession | None:
        """Return the stored boundary and journal documents, or ``None`` if unknown."""
        ...

    async def write(self, commit: SessionCommit) -> None:
        """Store the commit in one transaction.

        Insert every entry of ``commit.journal`` keyed by session and sequence,
        ignoring an entry that is already stored, and replace the stored boundary
        when ``commit.boundary`` is set. Domain rows mapped from the entries'
        records belong in the same transaction.
        """
        ...


def _decode_boundary(document: StoredDocument) -> SessionBoundary:
    if isinstance(document, str | bytes):
        return SessionBoundary.from_json(document)
    return SessionBoundary.from_dict(document)


def _decode_entry(document: StoredDocument) -> JournalEntry:
    if isinstance(document, str | bytes):
        return JournalEntry.from_json(document)
    return JournalEntry.from_dict(document)
