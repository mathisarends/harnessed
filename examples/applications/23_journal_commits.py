"""Run with: uv run python -m examples.applications.23_journal_commits"""

import asyncio
import json
import sqlite3
from contextlib import closing
from threading import Lock

from harnessed import Agent, Plugin, tool
from harnessed.sessions import SessionCommit, SessionStore, StoredSession
from harnessed.testing import ScriptedModel, call


class JournalStore(SessionStore):
    """Append journal entries per commit and replace the snapshot only when it changes.

    Both tables of one commit are written in a single transaction, so the stored
    journal always continues the stored snapshot. A new snapshot makes the
    entries before it redundant; this store prunes them, an audit store would
    keep them.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()
        self.snapshot_writes = 0
        self.entry_writes = 0
        self._connection.executescript(
            """
            CREATE TABLE snapshots (session TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE journal (
                session TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                body TEXT NOT NULL,
                PRIMARY KEY (session, sequence)
            );
            """
        )

    async def write(self, commit: SessionCommit) -> None:
        snapshot = commit.snapshot
        entries = [(entry.sequence, entry.to_json()) for entry in commit.journal]
        body = snapshot.to_json() if snapshot is not None else None
        since = snapshot.sequence if snapshot is not None else None
        await asyncio.to_thread(self._write, commit.session_id, entries, body, since)

    async def read(self, session_id: str) -> StoredSession | None:
        return await asyncio.to_thread(self._read, session_id)

    def _write(
        self, session: str, entries: list[tuple[int, str]], snapshot: str | None, since: int | None
    ) -> None:
        with self._lock, self._connection:
            self._connection.executemany(
                "INSERT INTO journal VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                [(session, sequence, body) for sequence, body in entries],
            )
            self.entry_writes += len(entries)
            if snapshot is not None:
                self._connection.execute(
                    "INSERT INTO snapshots VALUES (?, ?) "
                    "ON CONFLICT (session) DO UPDATE SET body = excluded.body",
                    (session, snapshot),
                )
                self._connection.execute(
                    "DELETE FROM journal WHERE session = ? AND sequence < ?", (session, since)
                )
                self.snapshot_writes += 1

    def _read(self, session: str) -> StoredSession | None:
        with self._lock:
            snapshot = self._connection.execute(
                "SELECT body FROM snapshots WHERE session = ?", (session,)
            ).fetchone()
            if snapshot is None:
                return None
            entries = self._connection.execute(
                "SELECT body FROM journal WHERE session = ? ORDER BY sequence", (session,)
            ).fetchall()
        return StoredSession(str(snapshot[0]), [str(entry[0]) for entry in entries])


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
        async with agent.session(id="support:4711", store=store) as session:
            await session.run("Which plan does the customer have?")
        # Barriers inside the turn appended small entries; the snapshot moved at its end.
        assert store.snapshot_writes < store.entry_writes

        async with agent.session(id="support:4711", store=store) as session:
            result = await session.run("Continue.")
        assert result.text == "Continued."
        print(
            f"{store.snapshot_writes} snapshot writes and "
            f"{store.entry_writes} journal entries reopened the conversation."
        )


if __name__ == "__main__":
    asyncio.run(main())
