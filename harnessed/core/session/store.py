from dataclasses import dataclass
from typing import Protocol

from harnessed.core.errors import SessionStateError


@dataclass(frozen=True)
class StoredSession:
    """An opaque Harnessed checkpoint and its repository revision."""

    payload: str
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.payload, str):
            raise TypeError("Session payloads must be strings")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Session revisions must be positive integers")


class SessionStore(Protocol):
    """Application-owned persistence; each save is an atomic compare-and-set.

    None expects a missing entry. Successful saves return the next positive
    revision; conflicts raise SessionWriteConflict. Acknowledgement means the
    write committed. Applications coordinate active workers separately: revision
    checks prevent lost updates, not duplicate external tool side effects.
    """

    async def load_session(self, conversation_id: str) -> StoredSession | None: ...

    async def save_session(
        self, conversation_id: str, payload: str, *, expected_revision: int | None
    ) -> int: ...


class SessionPersistence:
    """Bind one runtime to a repository revision; a failed write poisons the binding."""

    def __init__(self, store: SessionStore, conversation_id: str, revision: int | None) -> None:
        self._store = store
        self._conversation_id = conversation_id
        self._revision = revision
        self._failure: Exception | None = None

    def check(self) -> None:
        if self._failure is not None:
            raise self._failure

    async def save(self, payload: str) -> None:
        self.check()
        try:
            revision = await self._store.save_session(
                self._conversation_id, payload, expected_revision=self._revision
            )
            expected = 1 if self._revision is None else self._revision + 1
            if type(revision) is not int or revision != expected:
                raise SessionStateError("A session store must return the next revision")
            self._revision = revision
        except Exception as error:
            self._failure = error
            raise
