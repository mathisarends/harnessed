import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from harnessed.core.errors import SessionStateError
from harnessed.core.events.approvals import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
)
from harnessed.core.events.compaction import CompactionAppliedEvent
from harnessed.core.events.inbox import InboxAddedEvent, InboxRemovedEvent
from harnessed.core.events.messages import ContextValueChangedEvent
from harnessed.core.events.model import ModelRequestedEvent
from harnessed.core.events.session import SessionControlChangedEvent
from harnessed.core.events.tools import (
    ToolCallFinishedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
)
from harnessed.core.events.turn import TurnEndedEvent

from .checkpoint import CheckpointExtension, SessionCheckpoint
from .commit import SessionChange, SessionCommit


@dataclass(frozen=True)
class StoredSession:
    """A typed Harnessed checkpoint and its repository revision."""

    checkpoint: SessionCheckpoint
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, SessionCheckpoint):
            raise TypeError("Stored sessions require a SessionCheckpoint")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Session revisions must be positive integers")


class SessionStore(Protocol):
    """Application-owned persistence; each save is an atomic compare-and-set.

    None expects a missing entry. Successful saves return the next positive
    revision; conflicts raise SessionWriteConflict. Acknowledgement means the
    checkpoint and any application projection of its changes committed together.
    Stores may ignore changes when no projection is needed. Applications coordinate
    active workers separately: revision checks prevent lost updates, not duplicate
    external tool side effects.
    """

    async def load_session(self, conversation_id: str) -> StoredSession | None: ...

    async def save_session(
        self,
        conversation_id: str,
        commit: SessionCommit,
        *,
        expected_revision: int | None,
    ) -> int: ...


class SessionPersistence:
    """Coalesce intermediate state behind explicit durability barriers.

    The log owns state capture and the write lock. One delayed task requests the
    latest state; pending changes retain event references, not checkpoint copies.
    A failed write poisons the binding and notifies the active runtime.
    """

    def __init__(self, store: SessionStore, conversation_id: str, revision: int | None) -> None:
        self._store = store
        self._conversation_id = conversation_id
        self._revision = revision
        self._failure: Exception | None = None
        self._reported = False
        self._changes: list[SessionChange] = []
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

    def record(self, change: SessionChange) -> bool:
        self.check()
        self._changes.append(change)
        if self._closing:
            # Kernel-owned teardown can still remove named facts after the runtime
            # closes. Commit those changes inline without restarting the timer.
            return True
        if isinstance(
            change.event,
            InboxAddedEvent
            | InboxRemovedEvent
            | ModelRequestedEvent
            | ToolExecutionStartedEvent
            | ToolExecutionFinishedEvent
            | ToolCallFinishedEvent
            | TurnEndedEvent
            | SessionControlChangedEvent
            | ApprovalRequestedEvent
            | ApprovalResolvedEvent
            | ApprovalRevokedEvent
            | ContextValueChangedEvent
            | CompactionAppliedEvent
            | CheckpointExtension,
        ):
            return True
        self._schedule()
        return False

    async def flush(self, capture: Callable[[], SessionCheckpoint], *, report: bool = True) -> None:
        """Commit the latest dirty state while the owning log holds its lock."""
        self.check(report=report)
        if not self._changes:
            return
        try:
            commit = SessionCommit(capture(), tuple(self._changes))
            revision = await self._store.save_session(
                self._conversation_id, commit, expected_revision=self._revision
            )
            expected = 1 if self._revision is None else self._revision + 1
            if type(revision) is not int or revision != expected:
                raise SessionStateError("A session store must return the next revision")
            self._revision = revision
            self._changes.clear()
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
            if self._changes and self._failure is None:
                self._schedule()


_CHECKPOINT_INTERVAL = 0.1
