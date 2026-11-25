from collections.abc import Awaitable, Callable

from harnessed.sessions import (
    JournalEntry,
    SessionCommit,
    SessionRecovery,
    SessionSnapshot,
    SessionStore,
    StoredSession,
)


class CommitRecorder(SessionStore):
    """An application-owned session store that keeps every delivered commit.

    It stores each session like a database would: the latest snapshot and every
    journal entry keyed by its sequence. With ``documents=True`` both are kept as
    JSON documents and validated again on read, like JSONB columns.
    """

    def __init__(self, *, documents: bool = False) -> None:
        self.commits: list[SessionCommit] = []
        self.before: Callable[[SessionCommit], None] | None = None
        self._documents = documents
        self._boundaries: dict[str, SessionSnapshot | dict[str, object]] = {}
        self._journals: dict[str, dict[int, JournalEntry | dict[str, object]]] = {}

    async def write(self, commit: SessionCommit) -> None:
        if self.before is not None:
            self.before(commit)
        self.commits.append(commit)
        if commit.snapshot is not None:
            snapshot = commit.snapshot
            self._boundaries[commit.session_id] = (
                snapshot.to_dict() if self._documents else snapshot
            )
        journal = self._journals.setdefault(commit.session_id, {})
        for entry in commit.journal:
            journal[entry.sequence] = entry.to_dict() if self._documents else entry

    def put(self, checkpoint: SessionRecovery) -> None:
        """Store a checkpoint as if its snapshot and journal had been written."""
        session_id = checkpoint.session_id
        snapshot = checkpoint.snapshot
        self._boundaries[session_id] = snapshot.to_dict() if self._documents else snapshot
        self._journals[session_id] = {
            entry.sequence: entry.to_dict() if self._documents else entry
            for entry in checkpoint.journal
        }

    async def read(self, session_id: str) -> StoredSession | None:
        if session_id not in self._boundaries:
            return None
        journal = self._journals.get(session_id, {})
        entries = [journal[key] for key in sorted(journal)]
        return StoredSession(
            _document(self._boundaries[session_id]), [_document(entry) for entry in entries]
        )

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._boundaries

    def latest(self, session_id: str) -> SessionRecovery:
        snapshot = self._boundaries[session_id]
        entries = [self._journals[session_id][key] for key in sorted(self._journals[session_id])]
        if isinstance(snapshot, dict):
            return SessionRecovery.from_journal(
                SessionSnapshot.from_dict(snapshot),
                [JournalEntry.from_dict(entry) for entry in entries if isinstance(entry, dict)],
            )
        return SessionRecovery.from_journal(
            snapshot, [entry for entry in entries if isinstance(entry, JournalEntry)]
        )

    def session_ids(self) -> list[str]:
        return list(self._boundaries)

    def entries(self, session_id: str | None = None) -> list[JournalEntry]:
        """Delivered entries in journal order, without redelivered duplicates."""
        seen: dict[tuple[str, int], JournalEntry] = {}
        for commit in self.commits:
            if session_id is None or commit.session_id == session_id:
                for entry in commit.journal:
                    seen[commit.session_id, entry.sequence] = entry
        return list(seen.values())


class Writes(SessionStore):
    """A store that hands every commit to ``write`` and holds no sessions."""

    def __init__(self, write: Callable[[SessionCommit], Awaitable[None]]) -> None:
        self._write = write

    async def read(self, session_id: str) -> StoredSession | None:
        return None

    async def write(self, commit: SessionCommit) -> None:
        await self._write(commit)


def _document(value: SessionSnapshot | JournalEntry | dict[str, object]) -> dict[str, object]:
    return value if isinstance(value, dict) else value.to_dict()
