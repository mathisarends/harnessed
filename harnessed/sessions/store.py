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

from .commit import SessionCommit
from .journal import JournalEntry, SessionSnapshot
from .recovery import SessionRecovery

type StoredDocument = Mapping[str, JSONValue] | str | bytes
"""A stored snapshot or journal entry: a JSON document as a dict, text or bytes."""


@dataclass(frozen=True)
class StoredSession:
    """What a store holds for one session: its snapshot and the journal after it.

    ``journal`` lists the stored entries in sequence order. It may still contain
    entries from before the snapshot; they are skipped when the session reopens.
    """

    snapshot: StoredDocument
    journal: Sequence[StoredDocument] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "journal", tuple(self.journal))

    def recovery(self) -> SessionRecovery:
        """Decode the documents into the recovery value the session reopens from."""
        snapshot = _decode_snapshot(self.snapshot)
        entries = [_decode_entry(document) for document in self.journal]
        return SessionRecovery.from_journal(snapshot, entries)


@runtime_checkable
class SessionStore(Protocol):
    """Application-owned persistence for sessions, passed as ``agent.session(store=...)``.

    A session opened with an ``id`` reads its stored documents once and reopens
    from them. Afterwards it calls ``write`` at every durability barrier and
    waits for it to return. Child sessions write through their parent's store.
    """

    async def read(self, session_id: str) -> StoredSession | None:
        """Return the stored snapshot and journal documents, or ``None`` if unknown."""
        ...

    async def write(self, commit: SessionCommit) -> None:
        """Store the commit in one transaction.

        Insert every entry of ``commit.journal`` keyed by session and sequence,
        ignoring an entry that is already stored, and replace the stored snapshot
        when ``commit.snapshot`` is set. Domain rows mapped from the entries'
        records belong in the same transaction.
        """
        ...


def _decode_snapshot(document: StoredDocument) -> SessionSnapshot:
    if isinstance(document, str | bytes):
        return SessionSnapshot.from_json(document)
    return SessionSnapshot.from_dict(document)


def _decode_entry(document: StoredDocument) -> JournalEntry:
    if isinstance(document, str | bytes):
        return JournalEntry.from_json(document)
    return JournalEntry.from_dict(document)
