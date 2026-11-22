"""Run with: uv run python -m examples.applications.17_typed_documents"""

import asyncio

from examples.applications._conversation_repository import ConversationRepository
from harnessed import Agent
from harnessed.models import UserMessage
from harnessed.sessions import SessionCheckpoint, SessionCommit
from harnessed.testing import ScriptedModel


class ConversationCommits:
    """Application adapter; the repository and its rows do not import Harnessed.

    The checkpoint travels as a JSON-compatible document, as a JSONB column
    would hold it, and is validated into a typed value when it is read back.
    """

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def __call__(self, commit: SessionCommit) -> None:
        checkpoint = commit.checkpoint
        await self._repository.save(
            commit.session_id,
            document=checkpoint.to_dict(),
            format_version=checkpoint.version,
            retained_messages=len(checkpoint.state.history),
        )

    async def load(self, key: str) -> SessionCheckpoint | None:
        row = await self._repository.fetch(key)
        return SessionCheckpoint.from_dict(row.document) if row is not None else None


async def main() -> None:
    repository = ConversationRepository()
    commits = ConversationCommits(repository)
    model = ScriptedModel(["Remembered.", "Continued."])
    agent = Agent(model=model)
    async with agent.session(id="support:4711", on_commit=commits) as session:
        await session.run(UserMessage("Remember Ada.", id="customer-message-id"))

    row = await repository.fetch("support:4711")
    assert row is not None and row.retained_messages == 2
    state = await commits.load("support:4711")
    async with agent.session(state=state, on_commit=commits) as session:
        user = session.messages[0]
        assert isinstance(user, UserMessage) and user.id == "customer-message-id"
        print((await session.run("Continue.")).text)
    assert model.requests[-1].messages[0].text == "Remember Ada."


if __name__ == "__main__":
    asyncio.run(main())
