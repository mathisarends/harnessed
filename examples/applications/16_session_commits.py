"""Run with: uv run python -m examples.applications.16_session_commits"""

import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from harnessed import Agent
from harnessed.models import UserMessage
from harnessed.sessions import JournalEntry, SessionBoundary, SessionCheckpoint, SessionCommit
from harnessed.testing import ScriptedModel


class JournalFiles:
    """Application-owned storage: a boundary file and a JSONL journal per session.

    Harnessed calls the instance with each commit and waits for it to return.
    Every journal entry becomes one appended line; the boundary file is replaced
    only when a commit carries a new boundary. The two writes are not atomic, so
    a store that must survive a crash between them writes both in one database
    transaction instead, as the journal-commit example does.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    async def __call__(self, commit: SessionCommit) -> None:
        lines = "".join(f"{entry.to_json()}\n" for entry in commit.journal)
        boundary = commit.boundary.to_json() if commit.boundary is not None else None
        await asyncio.to_thread(self._write, commit.session_id, lines, boundary)

    async def load(self, session_id: str) -> SessionCheckpoint | None:
        if not self._path(session_id, "boundary.json").exists():
            return None
        boundary, lines = await asyncio.to_thread(self._read, session_id)
        entries = [JournalEntry.from_json(line) for line in lines]
        # Lines before the boundary stay in the file as an audit trail; they are skipped.
        return SessionCheckpoint.from_journal(SessionBoundary.from_json(boundary), entries)

    def _write(self, session_id: str, lines: str, boundary: str | None) -> None:
        with self._path(session_id, "journal.jsonl").open("a", encoding="utf-8") as journal:
            journal.write(lines)
        if boundary is not None:
            path = self._path(session_id, "boundary.json")
            temporary = path.with_suffix(".tmp")
            temporary.write_text(boundary, "utf-8")
            temporary.replace(path)

    def _read(self, session_id: str) -> tuple[str, list[str]]:
        boundary = self._path(session_id, "boundary.json").read_text("utf-8")
        journal = self._path(session_id, "journal.jsonl").read_text("utf-8")
        return boundary, journal.splitlines()

    def _path(self, session_id: str, suffix: str) -> Path:
        return self._root / f"{hashlib.sha256(session_id.encode()).hexdigest()}.{suffix}"


async def main() -> None:
    with TemporaryDirectory(prefix="harnessed-sessions-") as directory:
        files = JournalFiles(Path(directory))
        agent = Agent(model=ScriptedModel(["Remembered.", "Continued."]))
        async with agent.session(id="support:4711", on_commit=files) as session:
            await session.append_message(UserMessage("The customer is Ada."))
            first = await session.run("Remember the customer.")
            print(first.text)

        saved = await files.load("support:4711")
        async with agent.session(state=saved, on_commit=files) as restored:
            assert restored.messages[0].text == "The customer is Ada."
            second = await restored.run("Continue our conversation.")
            print(second.text)

        async with agent.session(id="queued", on_commit=files) as session:
            await session.pause()
            await session.submit("Wait for another request.")

        next_request = Agent(model=ScriptedModel(["Resumed."]))
        saved = await files.load("queued")
        async with next_request.session(state=saved, on_commit=files) as restored:
            await restored.resume()
            await restored.wait_until_idle()
            print(restored.messages[-1].text)


if __name__ == "__main__":
    asyncio.run(main())
