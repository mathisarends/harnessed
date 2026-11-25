from collections.abc import Callable

from harnessed.sessions import JournalEntry, SessionBoundary, SessionCheckpoint, SessionCommit


class CommitRecorder:
    """An application-owned commit handler that keeps every delivered commit.

    It stores each session like a database would: the latest boundary and every
    journal entry keyed by its sequence. With ``documents=True`` both are kept as
    JSON documents and validated again on read, like JSONB columns.
    """

    def __init__(self, *, documents: bool = False) -> None:
        self.commits: list[SessionCommit] = []
        self.before: Callable[[SessionCommit], None] | None = None
        self._documents = documents
        self._boundaries: dict[str, SessionBoundary | dict[str, object]] = {}
        self._journals: dict[str, dict[int, JournalEntry | dict[str, object]]] = {}

    async def __call__(self, commit: SessionCommit) -> None:
        if self.before is not None:
            self.before(commit)
        self.commits.append(commit)
        if commit.boundary is not None:
            boundary = commit.boundary
            self._boundaries[commit.session_id] = (
                boundary.to_dict() if self._documents else boundary
            )
        journal = self._journals.setdefault(commit.session_id, {})
        for entry in commit.journal:
            journal[entry.sequence] = entry.to_dict() if self._documents else entry

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._boundaries

    def latest(self, session_id: str) -> SessionCheckpoint:
        boundary = self._boundaries[session_id]
        entries = [self._journals[session_id][key] for key in sorted(self._journals[session_id])]
        if isinstance(boundary, dict):
            return SessionCheckpoint.from_journal(
                SessionBoundary.from_dict(boundary),
                [JournalEntry.from_dict(entry) for entry in entries if isinstance(entry, dict)],
            )
        return SessionCheckpoint.from_journal(
            boundary, [entry for entry in entries if isinstance(entry, JournalEntry)]
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
