from collections.abc import Callable

from harnessed.sessions import SessionChange, SessionCheckpoint, SessionCommit


class CommitRecorder:
    """An application-owned commit handler that keeps every delivered commit.

    With ``documents=True`` each checkpoint is stored as its JSON document and
    validated again on read, like a JSONB column.
    """

    def __init__(self, *, documents: bool = False) -> None:
        self.commits: list[SessionCommit] = []
        self.before: Callable[[SessionCommit], None] | None = None
        self._documents = documents
        self._rows: dict[str, dict[str, object]] = {}

    async def __call__(self, commit: SessionCommit) -> None:
        if self.before is not None:
            self.before(commit)
        self.commits.append(commit)
        self._rows[commit.session_id] = commit.checkpoint.to_dict()

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._rows

    def latest(self, session_id: str) -> SessionCheckpoint:
        if self._documents:
            return SessionCheckpoint.from_dict(self._rows[session_id])
        return next(
            commit.checkpoint
            for commit in reversed(self.commits)
            if commit.session_id == session_id
        )

    def session_ids(self) -> list[str]:
        return list(self._rows)

    def changes(self, session_id: str | None = None) -> list[SessionChange]:
        """Delivered changes in log order, without redelivered duplicates."""
        seen: dict[tuple[str, int], SessionChange] = {}
        for commit in self.commits:
            if session_id is None or commit.session_id == session_id:
                for change in commit.changes:
                    seen[commit.session_id, change.sequence] = change
        return list(seen.values())
