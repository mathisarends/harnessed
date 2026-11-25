import asyncio
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock


class Speaker(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"


@dataclass(frozen=True)
class MessageRow:
    key: str
    speaker: Speaker
    text: str


@dataclass(frozen=True)
class JournalRow:
    sequence: int
    body: bytes


@dataclass(frozen=True)
class StoredConversation:
    snapshot: bytes
    journal: tuple[bytes, ...]


class ConversationRepository:
    """Application-owned SQLite example, independent of the SDK and its models.

    ``conversations`` holds each session's latest snapshot and ``journal`` its
    append-only entries, keyed by sequence. Entries before the snapshot are kept
    as an audit trail. The caller owns the connection. A lock lets worker
    threads share this demo's single connection; a backend normally obtains a
    connection from its own pool.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()
        self._connection.executescript(
            """
            CREATE TABLE conversations (
                key TEXT PRIMARY KEY,
                snapshot BLOB NOT NULL
            );
            CREATE TABLE journal (
                conversation TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                body BLOB NOT NULL,
                PRIMARY KEY (conversation, sequence)
            );
            CREATE TABLE messages (
                conversation TEXT NOT NULL,
                key TEXT NOT NULL,
                speaker TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (conversation, key)
            );
            """
        )

    async def fetch(self, key: str) -> StoredConversation | None:
        return await asyncio.to_thread(self._fetch, key)

    async def messages(self, key: str) -> tuple[MessageRow, ...]:
        return await asyncio.to_thread(self._messages, key)

    async def commit(
        self,
        key: str,
        *,
        snapshot: bytes | None,
        journal: tuple[JournalRow, ...],
        messages: tuple[MessageRow, ...],
    ) -> None:
        await asyncio.to_thread(self._commit, key, snapshot, journal, messages)

    def _fetch(self, key: str) -> StoredConversation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT snapshot FROM conversations WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            entries = self._connection.execute(
                "SELECT body FROM journal WHERE conversation = ? ORDER BY sequence", (key,)
            ).fetchall()
            return StoredConversation(bytes(row[0]), tuple(bytes(entry[0]) for entry in entries))

    def _messages(self, key: str) -> tuple[MessageRow, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT key, speaker, text FROM messages WHERE conversation = ? ORDER BY rowid",
                (key,),
            ).fetchall()
            return tuple(MessageRow(str(row[0]), Speaker(row[1]), str(row[2])) for row in rows)

    def _commit(
        self,
        key: str,
        snapshot: bytes | None,
        journal: tuple[JournalRow, ...],
        messages: tuple[MessageRow, ...],
    ) -> None:
        with self._lock, self._connection:
            for message in messages:
                self._connection.execute(
                    """INSERT INTO messages VALUES (?, ?, ?, ?)
                    ON CONFLICT (conversation, key) DO UPDATE
                    SET speaker = excluded.speaker, text = excluded.text""",
                    (key, message.key, message.speaker, message.text),
                )
            for entry in journal:
                # A redelivered entry is already stored and stays unchanged.
                self._connection.execute(
                    "INSERT INTO journal VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    (key, entry.sequence, entry.body),
                )
            if snapshot is not None:
                self._connection.execute(
                    """INSERT INTO conversations VALUES (?, ?)
                    ON CONFLICT (key) DO UPDATE SET snapshot = excluded.snapshot""",
                    (key, snapshot),
                )
        # Exiting the connection context commits every table; exceptions roll all back.
