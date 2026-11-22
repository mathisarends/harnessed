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
                body BLOB NOT NULL
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

    async def fetch(self, key: str) -> bytes | None:
        return await asyncio.to_thread(self._fetch, key)

    async def messages(self, key: str) -> tuple[MessageRow, ...]:
        return await asyncio.to_thread(self._messages, key)

    async def commit(
        self,
        key: str,
        *,
        body: bytes,
        messages: tuple[MessageRow, ...],
    ) -> None:
        await asyncio.to_thread(self._commit, key, body, messages)

    def _fetch(self, key: str) -> bytes | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT body FROM conversations WHERE key = ?", (key,)
            ).fetchone()
            return None if row is None else bytes(row[0])

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
    ) -> None:
        with self._lock, self._connection:
            for message in messages:
                self._connection.execute(
                    """INSERT INTO messages VALUES (?, ?, ?, ?)
                    ON CONFLICT (conversation, key) DO UPDATE
                    SET speaker = excluded.speaker, text = excluded.text""",
                    (key, message.key, message.speaker, message.text),
                )
            self._connection.execute(
                """INSERT INTO conversations VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET body = excluded.body""",
                (key, body),
            )
        # Exiting the connection context commits both tables; exceptions roll both back.
