import asyncio

from examples.applications._atomic_repository import (
    ConversationRepository,
    JournalRow,
    MessageRow,
    Speaker,
)
from harnessed.events import AssistantMessageEvent, InboxAddedEvent
from harnessed.sessions import JournalEntry, SessionBoundary, SessionCheckpoint, SessionCommit


class ConversationCommits:
    """Translate SDK commits to application records in one repository transaction.

    The journal entries, a new boundary and the domain message rows mapped from
    the entries are written together. Message rows are upserted by their stable
    IDs and entries are keyed by sequence, so a commit redelivered after a
    failed transaction does not duplicate them.
    """

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def __call__(self, commit: SessionCommit) -> None:
        rows: list[MessageRow] = []
        for entry in commit.journal:
            event = entry.record
            if isinstance(event, InboxAddedEvent):
                rows.append(MessageRow(event.message.id, Speaker.CUSTOMER, event.message.text))
            elif isinstance(event, AssistantMessageEvent):
                rows.append(MessageRow(event.message.id, Speaker.AGENT, event.message.text))
        boundary, journal = await asyncio.to_thread(_encode, commit)
        await self._repository.commit(
            commit.session_id, boundary=boundary, journal=journal, messages=tuple(rows)
        )

    async def load(self, key: str) -> SessionCheckpoint | None:
        stored = await self._repository.fetch(key)
        if stored is None:
            return None
        boundary = await asyncio.to_thread(SessionBoundary.from_json, stored.boundary)
        entries = [JournalEntry.from_json(body) for body in stored.journal]
        return SessionCheckpoint.from_journal(boundary, entries)


def _encode(commit: SessionCommit) -> tuple[bytes | None, tuple[JournalRow, ...]]:
    """Serialize outside the event loop; a boundary holds the whole conversation."""
    boundary = commit.boundary.to_json().encode("utf-8") if commit.boundary is not None else None
    journal = tuple(
        JournalRow(entry.sequence, entry.to_json().encode("utf-8")) for entry in commit.journal
    )
    return boundary, journal
