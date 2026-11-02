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
class ConversationRow:
    body: bytes
    version: int


class ConversationConflictError(RuntimeError):
    pass


class ConversationRepository:
    """Application-owned SQLite example, independent of the SDK and its models.

    The caller owns the connection. A lock lets worker threads share this demo's
    single connection; a backend normally obtains a connection from its own pool.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = Lock()
        self._connection.executescript(
            """
            CREATE TABLE conversations (
                key TEXT PRIMARY KEY,
                body BLOB NOT NULL,
                version INTEGER NOT NULL
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

    async def fetch(self, key: str) -> ConversationRow | None:
        return await asyncio.to_thread(self._fetch, key)

    async def messages(self, key: str) -> tuple[MessageRow, ...]:
        return await asyncio.to_thread(self._messages, key)

    async def commit(
        self,
        key: str,
        *,
        body: bytes,
        messages: tuple[MessageRow, ...],
        expected_version: int | None,
    ) -> int:
        return await asyncio.to_thread(self._commit, key, body, messages, expected_version)

    def _fetch(self, key: str) -> ConversationRow | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT body, version FROM conversations WHERE key = ?", (key,)
            ).fetchone()
            return None if row is None else ConversationRow(bytes(row[0]), int(row[1]))

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
        body: bytes,
        messages: tuple[MessageRow, ...],
        expected_version: int | None,
    ) -> int:
        with self._lock, self._connection:
            for message in messages:
                self._connection.execute(
                    """INSERT INTO messages VALUES (?, ?, ?, ?)
                    ON CONFLICT (conversation, key) DO UPDATE
                    SET speaker = excluded.speaker, text = excluded.text""",
                    (key, message.key, message.speaker, message.text),
                )
            if expected_version is None:
                cursor = self._connection.execute(
                    "INSERT INTO conversations VALUES (?, ?, 1) ON CONFLICT (key) DO NOTHING",
                    (key, body),
                )
                version = 1
            else:
                cursor = self._connection.execute(
                    """UPDATE conversations SET body = ?, version = version + 1
                    WHERE key = ? AND version = ?""",
                    (body, key, expected_version),
                )
                version = expected_version + 1
            if cursor.rowcount != 1:
                raise ConversationConflictError(key)
        # Exiting the connection context commits both tables; exceptions roll both back.
        return version
