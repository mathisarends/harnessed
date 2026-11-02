import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from harnessed.core.errors import SessionStateError

from .events import (
    AgentControlChangedEvent,
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
    CompactionAppliedEvent,
    ContextValueChangedEvent,
    DurableEvent,
    InboxAddedEvent,
    InboxRemovedEvent,
    ModelRequestedEvent,
    SessionEvent,
    ToolCallFinishedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    TurnEndedEvent,
)


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
    """Coalesce intermediate state behind explicit durability barriers.

    The log owns serialization and the write lock. One delayed task requests the
    latest state, so intermediate events never enqueue copies of checkpoints.
    A failed write poisons the binding and notifies the active runtime.
    """

    def __init__(self, store: SessionStore, conversation_id: str, revision: int | None) -> None:
        self._store = store
        self._conversation_id = conversation_id
        self._revision = revision
        self._failure: Exception | None = None
        self._reported = False
        self._dirty = False
        self._closing = False
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._flush: Callable[[], Awaitable[None]] | None = None
        self._on_failure: Callable[[Exception], None] | None = None

    def bind(self, flush: Callable[[], Awaitable[None]]) -> None:
        self._flush = flush

    def set_error_handler(self, callback: Callable[[Exception], None]) -> None:
        self._on_failure = callback

    @property
    def failed(self) -> bool:
        return self._failure is not None

    def check(self, *, report: bool = True) -> None:
        if self._failure is not None:
            if report:
                self._reported = True
            raise self._failure

    def record(self, event: SessionEvent) -> bool:
        self.check()
        self._dirty = True
        if self._closing:
            # Kernel-owned teardown can still remove named facts after the runtime
            # closes. Commit those changes inline without restarting the timer.
            return True
        if isinstance(
            event,
            InboxAddedEvent
            | InboxRemovedEvent
            | ModelRequestedEvent
            | ToolExecutionStartedEvent
            | ToolExecutionFinishedEvent
            | ToolCallFinishedEvent
            | TurnEndedEvent
            | AgentControlChangedEvent
            | ApprovalRequestedEvent
            | ApprovalResolvedEvent
            | ApprovalRevokedEvent
            | ContextValueChangedEvent
            | CompactionAppliedEvent
            | DurableEvent,
        ):
            return True
        self._schedule()
        return False

    async def flush(self, serialize: Callable[[], Awaitable[str]], *, report: bool = True) -> None:
        """Commit the latest dirty state while the owning log holds its lock."""
        self.check(report=report)
        if not self._dirty:
            return
        try:
            payload = await serialize()
            revision = await self._store.save_session(
                self._conversation_id, payload, expected_revision=self._revision
            )
            expected = 1 if self._revision is None else self._revision + 1
            if type(revision) is not int or revision != expected:
                raise SessionStateError("A session store must return the next revision")
            self._revision = revision
            self._dirty = False
        except Exception as error:
            self._failure = error
            if report:
                self._reported = True
            raise

    async def aclose(self) -> None:
        """Drain pending work; already reported failures are not raised twice."""
        self._closing = True
        self._wake.set()
        try:
            task = self._task
            if task is not None:
                await asyncio.shield(task)
            if self._failure is not None:
                if not self._reported:
                    self.check()
                return
            flush = self._flush
            if flush is not None:
                await flush()
        finally:
            self._flush = None
            self._on_failure = None

    def _schedule(self) -> None:
        if self._task is None and not self._closing:
            self._task = asyncio.create_task(self._deferred(), name="harnessed:persistence")

    async def _deferred(self) -> None:
        try:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=_CHECKPOINT_INTERVAL)
            except TimeoutError:
                pass
            flush = self._flush
            if flush is not None:
                await flush()
        except Exception as error:
            self._failure = error
            callback = self._on_failure
            if callback is not None:
                callback(error)
        finally:
            self._task = None
            if self._dirty and self._failure is None:
                self._schedule()


_CHECKPOINT_INTERVAL = 0.1
