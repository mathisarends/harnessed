"""Run with: uv run python -m examples.applications.16_session_commits"""

import asyncio
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from harnessed import Agent
from harnessed.models import UserMessage
from harnessed.sessions import SessionCheckpoint, SessionCommit
from harnessed.testing import ScriptedModel


class CheckpointFiles:
    """Application-owned storage: one JSON checkpoint file per session.

    Harnessed calls the instance with each commit and waits for it to return;
    the application decides where and how the checkpoint is kept.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    async def __call__(self, commit: SessionCommit) -> None:
        document = commit.checkpoint.to_json()
        await asyncio.to_thread(self._path(commit.session_id).write_text, document, "utf-8")

    async def load(self, session_id: str) -> SessionCheckpoint | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        document = await asyncio.to_thread(path.read_text, "utf-8")
        return SessionCheckpoint.from_json(document)

    def _path(self, session_id: str) -> Path:
        return self._root / f"{hashlib.sha256(session_id.encode()).hexdigest()}.json"


async def main() -> None:
    with TemporaryDirectory(prefix="harnessed-sessions-") as directory:
        files = CheckpointFiles(Path(directory))
        agent = Agent(model=ScriptedModel(["Remembered.", "Continued."]))
        async with agent.session(id="support:4711", on_commit=files) as session:
            await session.append_message(UserMessage("The customer is Ada."))
            first = await session.run("Remember the customer.")
            print(first.text)

        saved = await files.load("support:4711")
        async with agent.session(id="support:4711", state=saved, on_commit=files) as restored:
            assert restored.messages[0].text == "The customer is Ada."
            second = await restored.run("Continue our conversation.")
            print(second.text)

        async with agent.session(id="queued", on_commit=files) as session:
            await session.pause()
            await session.submit("Wait for another request.")

        next_request = Agent(model=ScriptedModel(["Resumed."]))
        saved = await files.load("queued")
        async with next_request.session(id="queued", state=saved, on_commit=files) as restored:
            await restored.resume()
            await restored.wait_until_idle()
            print(restored.messages[-1].text)


if __name__ == "__main__":
    asyncio.run(main())
