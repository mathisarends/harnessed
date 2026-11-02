import asyncio

from examples._atomic_repository import (
    ConversationConflictError,
    ConversationRepository,
    MessageRow,
    Speaker,
)
from harnessed.errors import SessionWriteConflict
from harnessed.events import AssistantMessageEvent, InboxAddedEvent
from harnessed.sessions import SessionCheckpoint, SessionCommit, StoredSession


class ConversationSessionStore:
    """Translate SDK commits to application records in one repository transaction."""

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
        rows: list[MessageRow] = []
        for change in commit.changes:
            event = change.event
            if isinstance(event, InboxAddedEvent):
                rows.append(MessageRow(event.message.id, Speaker.CUSTOMER, event.message.text))
            elif isinstance(event, AssistantMessageEvent):
                rows.append(MessageRow(event.message.id, Speaker.AGENT, event.message.text))
        document = await asyncio.to_thread(commit.checkpoint.to_json)
        try:
            return await self._repository.commit(
                conversation_id,
                body=document.encode("utf-8"),
                messages=tuple(rows),
                expected_version=expected_revision,
            )
        except ConversationConflictError as error:
            raise SessionWriteConflict(conversation_id) from error
