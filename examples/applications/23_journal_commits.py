"""Run with: uv run python -m examples.applications.23_journal_commits"""

import asyncio
import json
import sqlite3
from contextlib import closing
from threading import Lock

from harnessed import Agent, Plugin, tool
from harnessed.sessions import JournalEntry, SessionBoundary, SessionCheckpoint, SessionCommit
from harnessed.testing import ScriptedModel, call


class JournalStore:
    """Append journal entries per commit and replace the boundary only when it changes.

    Both tables of one commit are written in a single transaction, so the stored
    journal always continues the stored boundary. A new boundary makes the
    entries before it redundant; this store prunes them, an audit store would
    keep them.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()
        self.boundary_writes = 0
        self.entry_writes = 0
        self._connection.executescript(
            """
            CREATE TABLE boundaries (session TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE journal (
                session TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                body TEXT NOT NULL,
                PRIMARY KEY (session, sequence)
            );
            """
        )

    async def __call__(self, commit: SessionCommit) -> None:
        boundary = commit.boundary
        entries = [(entry.sequence, entry.to_json()) for entry in commit.journal]
        body = boundary.to_json() if boundary is not None else None
        since = boundary.sequence if boundary is not None else None
        await asyncio.to_thread(self._write, commit.session_id, entries, body, since)

    async def load(self, session: str) -> SessionCheckpoint:
        boundary, entries = await asyncio.to_thread(self._read, session)
        journal = [JournalEntry.from_json(entry) for entry in entries]
        return SessionCheckpoint.from_journal(SessionBoundary.from_json(boundary), journal)

    def _write(
        self, session: str, entries: list[tuple[int, str]], boundary: str | None, since: int | None
    ) -> None:
        with self._lock, self._connection:
            self._connection.executemany(
                "INSERT INTO journal VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                [(session, sequence, body) for sequence, body in entries],
            )
            self.entry_writes += len(entries)
            if boundary is not None:
                self._connection.execute(
                    "INSERT INTO boundaries VALUES (?, ?) "
                    "ON CONFLICT (session) DO UPDATE SET body = excluded.body",
                    (session, boundary),
                )
                self._connection.execute(
                    "DELETE FROM journal WHERE session = ? AND sequence < ?", (session, since)
                )
                self.boundary_writes += 1

    def _read(self, session: str) -> tuple[str, list[str]]:
        with self._lock:
            boundary = self._connection.execute(
                "SELECT body FROM boundaries WHERE session = ?", (session,)
            ).fetchone()
            entries = self._connection.execute(
                "SELECT body FROM journal WHERE session = ? ORDER BY sequence", (session,)
            ).fetchall()
        return str(boundary[0]), [str(entry[0]) for entry in entries]


class Notes(Plugin):
    @tool(readonly=True)
    async def read_notes(self) -> str:
        """Read the project notes."""
        return json.dumps({"customer": "Ada", "plan": "pro"})


async def main() -> None:
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        store = JournalStore(connection)
        model = ScriptedModel([call("read_notes"), "Ada is on the pro plan.", "Continued."])
        agent = Agent(model=model, plugins=[Notes()])
        async with agent.session(id="support:4711", on_commit=store) as session:
            await session.run("Which plan does the customer have?")
        # Barriers inside the turn appended small entries; the boundary moved at its end.
        assert store.boundary_writes < store.entry_writes

        checkpoint = await store.load("support:4711")
        async with agent.session(state=checkpoint, on_commit=store) as session:
            result = await session.run("Continue.")
        assert result.text == "Continued."
        print(
            f"{store.boundary_writes} boundary writes and "
            f"{store.entry_writes} journal entries reopened the conversation."
        )


if __name__ == "__main__":
    asyncio.run(main())
