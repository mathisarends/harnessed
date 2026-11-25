"""Run with: uv run python -m examples.applications.17_typed_documents"""

import asyncio

from examples.applications._conversation_repository import ConversationRepository
from harnessed import Agent
from harnessed.models import UserMessage
from harnessed.sessions import SessionCommit, SessionStore, StoredSession
from harnessed.testing import ScriptedModel


class ConversationStore(SessionStore):
    """Application adapter; the repository and its rows do not import Harnessed.

    Snapshots and journal entries travel as JSON-compatible dicts, as JSONB
    columns would hold them. Harnessed validates them into typed values when the
    session reopens. The typed commit also feeds plain application columns.
    """

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def read(self, session_id: str) -> StoredSession | None:
        row = await self._repository.fetch(session_id)
        if row is None:
            return None
        return StoredSession(row.snapshot, [row.journal[key] for key in sorted(row.journal)])

    async def write(self, commit: SessionCommit) -> None:
        snapshot = commit.snapshot
        await self._repository.save(
            commit.session_id,
            journal={entry.sequence: entry.to_dict() for entry in commit.journal},
            snapshot=snapshot.to_dict() if snapshot is not None else None,
            format_version=snapshot.version if snapshot is not None else 0,
            retained_messages=len(snapshot.state.history) if snapshot is not None else 0,
        )


async def main() -> None:
    repository = ConversationRepository()
    store = ConversationStore(repository)
    model = ScriptedModel(["Remembered.", "Continued."])
    agent = Agent(model=model)
    async with agent.session(id="support:4711", store=store) as session:
        await session.run(UserMessage("Remember Ada.", id="customer-message-id"))

    row = await repository.fetch("support:4711")
    assert row is not None and row.retained_messages == 2
    async with agent.session(id="support:4711", store=store) as session:
        user = session.messages[0]
        assert isinstance(user, UserMessage) and user.id == "customer-message-id"
        print((await session.run("Continue.")).text)
    assert model.requests[-1].messages[0].text == "Remember Ada."


if __name__ == "__main__":
    asyncio.run(main())
