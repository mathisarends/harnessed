"""Run with: uv run python -m examples.applications.16_session_commits"""

import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from harnessed import Agent
from harnessed.models import UserMessage
from harnessed.sessions import SessionCommit, SessionStore, StoredSession
from harnessed.testing import ScriptedModel


class JournalFiles(SessionStore):
    """Application-owned storage: a snapshot file and a JSONL journal per session.

    Every journal entry becomes one appended line; the snapshot file is replaced
    only when a commit carries a new snapshot. Lines before the snapshot stay in
    the file as an audit trail and are skipped on reopening. The two writes are
    not atomic, so a store that must survive a crash between them writes both in
    one database transaction instead, as the journal-commit example does.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    async def read(self, session_id: str) -> StoredSession | None:
        return await asyncio.to_thread(self._read, session_id)

    async def write(self, commit: SessionCommit) -> None:
        lines = "".join(f"{entry.to_json()}\n" for entry in commit.journal)
        snapshot = commit.snapshot.to_json() if commit.snapshot is not None else None
        await asyncio.to_thread(self._write, commit.session_id, lines, snapshot)

    def _read(self, session_id: str) -> StoredSession | None:
        snapshot = self._path(session_id, "snapshot.json")
        if not snapshot.exists():
            return None
        journal = self._path(session_id, "journal.jsonl").read_text("utf-8")
        return StoredSession(snapshot.read_text("utf-8"), journal.splitlines())

    def _write(self, session_id: str, lines: str, snapshot: str | None) -> None:
        with self._path(session_id, "journal.jsonl").open("a", encoding="utf-8") as journal:
            journal.write(lines)
        if snapshot is not None:
            path = self._path(session_id, "snapshot.json")
            temporary = path.with_suffix(".tmp")
            temporary.write_text(snapshot, "utf-8")
            temporary.replace(path)

    def _path(self, session_id: str, suffix: str) -> Path:
        return self._root / f"{hashlib.sha256(session_id.encode()).hexdigest()}.{suffix}"


async def main() -> None:
    with TemporaryDirectory(prefix="harnessed-sessions-") as directory:
        files = JournalFiles(Path(directory))
        agent = Agent(model=ScriptedModel(["Remembered.", "Continued."]))
        async with agent.session(id="support:4711", store=files) as session:
            await session.append_message(UserMessage("The customer is Ada."))
            first = await session.run("Remember the customer.")
            print(first.text)

        # The same id reopens the stored conversation.
        async with agent.session(id="support:4711", store=files) as restored:
            assert restored.messages[0].text == "The customer is Ada."
            second = await restored.run("Continue our conversation.")
            print(second.text)

        async with agent.session(id="queued", store=files) as session:
            await session.pause()
            await session.submit("Wait for another request.")

        next_request = Agent(model=ScriptedModel(["Resumed."]))
        async with next_request.session(id="queued", store=files) as restored:
            await restored.resume()
            await restored.wait_until_idle()
            print(restored.messages[-1].text)


if __name__ == "__main__":
    asyncio.run(main())
