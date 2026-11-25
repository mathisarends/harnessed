from examples.applications._atomic_repository import (
    ConversationRepository,
    JournalRow,
    MessageRow,
    Speaker,
)
from harnessed.events import AssistantMessageEvent, InboxAddedEvent
from harnessed.sessions import SessionCommit, SessionStore, StoredSession


class ConversationStore(SessionStore):
    """Translate SDK commits to application records in one repository transaction.

    The journal entries, a new boundary and the domain message rows mapped from
    the entries are written together. Message rows are upserted by their stable
    IDs and entries are keyed by sequence, so a commit redelivered after a
    failed transaction does not duplicate them.
    """

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def read(self, session_id: str) -> StoredSession | None:
        stored = await self._repository.fetch(session_id)
        if stored is None:
            return None
        return StoredSession(stored.boundary, stored.journal)

    async def write(self, commit: SessionCommit) -> None:
        rows: list[MessageRow] = []
        for entry in commit.journal:
            event = entry.record
            if isinstance(event, InboxAddedEvent):
                rows.append(MessageRow(event.message.id, Speaker.CUSTOMER, event.message.text))
            elif isinstance(event, AssistantMessageEvent):
                rows.append(MessageRow(event.message.id, Speaker.AGENT, event.message.text))
        journal = tuple(
            JournalRow(entry.sequence, entry.to_json().encode("utf-8")) for entry in commit.journal
        )
        boundary = (
            commit.boundary.to_json().encode("utf-8") if commit.boundary is not None else None
        )
        await self._repository.commit(
            commit.session_id, boundary=boundary, journal=journal, messages=tuple(rows)
        )
