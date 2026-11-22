import asyncio

from examples.applications._atomic_repository import ConversationRepository, MessageRow, Speaker
from harnessed.events import AssistantMessageEvent, InboxAddedEvent
from harnessed.sessions import SessionCheckpoint, SessionCommit


class ConversationCommits:
    """Translate SDK commits to application records in one repository transaction.

    Message rows are upserted by their stable IDs, so a commit redelivered
    after a failed transaction does not duplicate them.
    """

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def __call__(self, commit: SessionCommit) -> None:
        rows: list[MessageRow] = []
        for change in commit.changes:
            event = change.event
            if isinstance(event, InboxAddedEvent):
                rows.append(MessageRow(event.message.id, Speaker.CUSTOMER, event.message.text))
            elif isinstance(event, AssistantMessageEvent):
                rows.append(MessageRow(event.message.id, Speaker.AGENT, event.message.text))
        document = await asyncio.to_thread(commit.checkpoint.to_json)
        await self._repository.commit(
            commit.session_id, body=document.encode("utf-8"), messages=tuple(rows)
        )

    async def load(self, key: str) -> SessionCheckpoint | None:
        body = await self._repository.fetch(key)
        if body is None:
            return None
        return await asyncio.to_thread(SessionCheckpoint.from_json, body)
