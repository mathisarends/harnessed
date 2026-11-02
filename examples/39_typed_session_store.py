"""Run with: uv run python -m examples.39_typed_session_store"""

import asyncio

from examples._conversation_repository import ConversationConflictError, ConversationRepository
from harnessed import Agent
from harnessed.errors import SessionWriteConflict
from harnessed.models import UserMessage
from harnessed.sessions import SessionCheckpoint, SessionCommit, SessionStore, StoredSession
from harnessed.testing import ScriptedModel


class ConversationSessionStore:
    """Application adapter; the repository and its models do not import Harnessed."""

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def load_session(self, conversation_id: str) -> StoredSession | None:
        row = await self._repository.fetch(conversation_id)
        if row is None:
            return None
        checkpoint = await asyncio.to_thread(SessionCheckpoint.from_json, row.body)
        return StoredSession(checkpoint, row.version)

    async def save_session(
        self,
        conversation_id: str,
        commit: SessionCommit,
        *,
        expected_revision: int | None,
    ) -> int:
        checkpoint = commit.checkpoint
        document = await asyncio.to_thread(checkpoint.to_json)
        try:
            return await self._repository.commit(
                conversation_id,
                body=document.encode("utf-8"),
                retained_messages=len(checkpoint.state.history),
                expected_version=expected_revision,
            )
        except ConversationConflictError as error:
            raise SessionWriteConflict(conversation_id) from error


async def main() -> None:
    repository = ConversationRepository()
    store: SessionStore = ConversationSessionStore(repository)
    model = ScriptedModel(["Remembered.", "Continued."])
    agent = Agent(model=model, store=store)
    async with agent.session(id="support:4711") as session:
        await session.run(UserMessage("Remember Ada.", id="customer-message-id"))

    row = await repository.fetch("support:4711")
    assert row is not None and row.retained_messages == 2
    async with agent.session(id="support:4711") as session:
        user = session.messages[0]
        assert isinstance(user, UserMessage) and user.id == "customer-message-id"
        print((await session.run("Continue.")).text)
    assert model.requests[-1].messages[0].text == "Remember Ada."


if __name__ == "__main__":
    asyncio.run(main())
