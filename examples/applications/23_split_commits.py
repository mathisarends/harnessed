"""Run with: uv run python -m examples.applications.23_split_commits"""

import asyncio
import json
import sqlite3
from contextlib import closing
from threading import Lock

from harnessed import Agent, Plugin, tool
from harnessed.sessions import SessionCheckpoint, SessionCommit, SessionExecution, SessionState
from harnessed.testing import ScriptedModel, call


class SplitCheckpoints:
    """Write the conversation state once per change and the small execution part per commit.

    Both rows of one commit are written in a single transaction, so a stored
    execution always belongs to the stored state.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()
        self.state_writes = 0
        self.execution_writes = 0
        self._connection.executescript(
            """
            CREATE TABLE states (session TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE executions (session TEXT PRIMARY KEY, body TEXT NOT NULL);
            """
        )

    async def __call__(self, commit: SessionCommit) -> None:
        state = commit.checkpoint.state.to_json() if commit.state_changed else None
        execution = commit.checkpoint.execution.to_json()
        await asyncio.to_thread(self._write, commit.session_id, state, execution)

    async def load(self, session: str) -> SessionCheckpoint:
        state, execution = await asyncio.to_thread(self._read, session)
        return SessionCheckpoint.from_parts(
            SessionState.from_json(state), SessionExecution.from_json(execution)
        )

    def _write(self, session: str, state: str | None, execution: str) -> None:
        with self._lock, self._connection:
            if state is not None:
                self._connection.execute(
                    "INSERT INTO states VALUES (?, ?) "
                    "ON CONFLICT (session) DO UPDATE SET body = excluded.body",
                    (session, state),
                )
                self.state_writes += 1
            self._connection.execute(
                "INSERT INTO executions VALUES (?, ?) "
                "ON CONFLICT (session) DO UPDATE SET body = excluded.body",
                (session, execution),
            )
            self.execution_writes += 1

    def _read(self, session: str) -> tuple[str, str]:
        with self._lock:
            state = self._connection.execute(
                "SELECT body FROM states WHERE session = ?", (session,)
            ).fetchone()
            execution = self._connection.execute(
                "SELECT body FROM executions WHERE session = ?", (session,)
            ).fetchone()
        return str(state[0]), str(execution[0])


class Notes(Plugin):
    @tool(readonly=True)
    async def read_notes(self) -> str:
        """Read the project notes."""
        return json.dumps({"customer": "Ada", "plan": "pro"})


async def main() -> None:
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        store = SplitCheckpoints(connection)
        model = ScriptedModel([call("read_notes"), "Ada is on the pro plan.", "Continued."])
        agent = Agent(model=model, plugins=[Notes()])
        async with agent.session(id="support:4711", on_commit=store) as session:
            await session.run("Which plan does the customer have?")
        # Barriers inside the turn wrote only the execution part.
        assert store.state_writes < store.execution_writes

        checkpoint = await store.load("support:4711")
        async with agent.session(state=checkpoint, on_commit=store) as session:
            result = await session.run("Continue.")
        assert result.text == "Continued."
        print(
            f"{store.state_writes} state writes and "
            f"{store.execution_writes} execution writes reopened the conversation."
        )


if __name__ == "__main__":
    asyncio.run(main())
