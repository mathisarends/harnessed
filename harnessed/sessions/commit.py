import asyncio
from collections.abc import Awaitable, Callable
from itertools import pairwise
from typing import Annotated, NamedTuple

from pydantic import Field
from pydantic.dataclasses import dataclass

from harnessed.errors import CommitTimeout, SessionStateError
from harnessed.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
    CompactionAppliedEvent,
    InboxAddedEvent,
    InboxRemovedEvent,
    PluginValueChangedEvent,
    SessionControlChangedEvent,
    ToolCallFinishedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    TurnEndedEvent,
)

from .journal import (
    STORAGE_CONFIG,
    CheckpointExtension,
    CheckpointRequest,
    JournalEntry,
    Resources,
    SessionBoundary,
    StoredResources,
    attach_resources,
)
from .state import SessionState

_BARRIERS = (
    InboxAddedEvent
    | InboxRemovedEvent
    | CheckpointRequest
    | ToolExecutionStartedEvent
    | ToolExecutionFinishedEvent
    | ToolCallFinishedEvent
    | TurnEndedEvent
    | SessionControlChangedEvent
    | ApprovalRequestedEvent
    | ApprovalResolvedEvent
    | ApprovalRevokedEvent
    | PluginValueChangedEvent
    | CompactionAppliedEvent
    | CheckpointExtension
)


@dataclass(frozen=True, config=STORAGE_CONFIG)
class SessionCommit:
    """The journal entries a session wrote since its previous commit.

    Append ``journal`` to the session's stored entries. When ``boundary`` is not
    None, the session reached a new rest point: replace the stored boundary in
    the same transaction. Entries before the stored boundary are no longer
    needed to reopen the session; keep them as an audit trail or delete them.

    Sequences are stable and contiguous, so a store can key entries by
    ``(session_id, sequence)`` and ignore a redelivered entry it already holds.
    """

    __hash__ = None

    session_id: Annotated[str, Field(min_length=1)]
    journal: tuple[JournalEntry, ...]
    boundary: SessionBoundary | None = None

    def __post_init__(self) -> None:
        if not self.journal:
            raise ValueError("A commit carries at least one journal entry")
        if any(
            following.sequence != previous.sequence + 1
            for previous, following in pairwise(self.journal)
        ):
            raise ValueError("Journal entries must have contiguous ascending sequences")
        boundary = self.boundary
        if boundary is not None:
            if boundary.session_id != self.session_id:
                raise ValueError("A commit's boundary must belong to its session")
            if boundary.sequence > self.next_sequence:
                raise ValueError("A commit's boundary cannot lie beyond its journal")

    @property
    def next_sequence(self) -> int:
        """The first journal position after this commit."""
        return self.journal[-1].sequence + 1


type CommitHandler = Callable[[SessionCommit], Awaitable[None]]
"""An application callback that stores one commit before returning."""


class CommitCapture(NamedTuple):
    """What a session hands to a delivery: its current boundary and every resource it holds."""

    boundary: SessionBoundary
    resources: Resources


class CommitDelivery:
    """Deliver new journal entries to an application handler at barriers.

    Input admission, model request and tool execution intent, tool results,
    turn completion, controls, approvals, plugin values, compaction and custom
    durable events are barriers: their append returns only after the handler
    returned. Other entries ride along with the next barrier or explicit flush.
    A failing handler raises to the appending caller and rejects every later
    admission with the same error: the application's last stored commit stays
    the truth, and the conversation continues by reopening from it.

    A commit carries a new boundary when the conversation state differs from
    the last delivered boundary's state. Resources travel once: with the
    boundary that holds them, or on the last entry of the first commit after
    the session stored them.

    The handler runs while the log holds its admission lock, so it must not
    write to or flush its own log. ``timeout_seconds`` bounds each delivery;
    an expired delivery fails like a raising handler, with ``CommitTimeout``.
    """

    def __init__(self, handler: CommitHandler, *, timeout_seconds: float | None = None) -> None:
        self.handler = handler
        self.timeout_seconds = timeout_seconds
        self._pending: list[JournalEntry] = []
        self._closing = False
        self._failure: BaseException | None = None
        self._delivering: asyncio.Task[object] | None = None
        self._state: SessionState | None = None
        self._stored = StoredResources()

    @property
    def failed(self) -> bool:
        """Whether a delivery raised; later admissions are rejected."""
        return self._failure is not None

    def check(self) -> None:
        """Reject admission after a failed delivery."""
        if self._failure is not None:
            raise self._failure

    def check_reentry(self) -> None:
        """Reject a write or flush from the task that is running the handler.

        Tasks the handler spawns cannot be detected; they wait for the lock.
        """
        task = asyncio.current_task()
        if task is not None and task is self._delivering:
            raise SessionStateError("An on_commit handler cannot write to or flush its own session")

    def record(self, entry: JournalEntry) -> bool:
        """Queue an entry and report whether it must be delivered before admission returns."""
        self._pending.append(entry)
        # Kernel-owned teardown can still remove named facts after the runtime closes.
        return self._closing or isinstance(entry.record, _BARRIERS)

    async def deliver(self, capture: Callable[[], CommitCapture]) -> None:
        """Hand pending entries to the application while the owning log holds its lock."""
        if not self._pending:
            return
        current = capture()
        boundary = current.boundary if self._is_new(current.boundary.state) else None
        stored = StoredResources.of(boundary) if boundary is not None else self._stored
        added = stored.missing(current.resources)
        journal = attach_resources(self._pending, added)
        commit = SessionCommit(current.boundary.session_id, journal, boundary=boundary)
        await self._hand_over(commit)
        if boundary is not None:
            self._state = boundary.state
        stored.add(added)
        self._stored = stored
        self._pending.clear()

    def close(self) -> None:
        """Deliver every later change immediately."""
        self._closing = True

    async def _hand_over(self, commit: SessionCommit) -> None:
        deadline = asyncio.timeout(self.timeout_seconds)
        self._delivering = asyncio.current_task()
        try:
            async with deadline:
                await self.handler(commit)
        except Exception as error:
            if isinstance(error, TimeoutError) and deadline.expired():
                timeout = CommitTimeout(
                    f"The on_commit handler did not return within {self.timeout_seconds} seconds"
                )
                self._failure = timeout
                raise timeout from error
            self._failure = error
            raise
        finally:
            self._delivering = None

    def _is_new(self, state: SessionState) -> bool:
        """Whether the state differs from the last delivered boundary's state."""
        previous = self._state
        if previous is state:
            return False
        if previous is None or previous != state:
            return True
        # Python equality also equates true, 1 and 1.0 in nested JSON data.
        return previous.content_sha256 != state.content_sha256
