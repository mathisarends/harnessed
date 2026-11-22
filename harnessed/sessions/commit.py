import asyncio
from collections.abc import Awaitable, Callable
from itertools import pairwise
from typing import Annotated

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
    ModelRequestedEvent,
    PluginValueChangedEvent,
    SessionControlChangedEvent,
    ToolCallFinishedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
    TurnEndedEvent,
)
from harnessed.events.codec import validate_event
from harnessed.models.values import VALUE_CONFIG

from .checkpoint import CheckpointEntry, CheckpointExtension, SessionCheckpoint
from .state import SessionState

_BARRIERS = (
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
    | PluginValueChangedEvent
    | CompactionAppliedEvent
    | CheckpointExtension
)


@dataclass(frozen=True, config=VALUE_CONFIG)
class SessionChange:
    """One new durable event at its conversation-local, zero-based log position."""

    __hash__ = None

    sequence: Annotated[int, Field(ge=0)]
    event: CheckpointEntry

    def __post_init__(self) -> None:
        if not isinstance(self.event, CheckpointExtension):
            validate_event(self.event)


@dataclass(frozen=True, config=VALUE_CONFIG)
class SessionCommit:
    """A session's complete checkpoint and the ordered changes since its last commit.

    Persist the checkpoint to reopen the session with ``Agent.session(state=...)``,
    and map the changes into application records in the same transaction.
    Sequences are stable, so applications can upsert redelivered changes. Neither
    value dictates the backend's schema.

    ``state_changed`` is false when ``checkpoint.state`` equals the state of the
    session's previous delivered commit; a store may then write only
    ``checkpoint.execution``. It is true for a session's first commit.
    """

    __hash__ = None

    checkpoint: SessionCheckpoint
    changes: tuple[SessionChange, ...] = ()
    state_changed: bool = True

    def __post_init__(self) -> None:
        if any(
            following.sequence != previous.sequence + 1
            for previous, following in pairwise(self.changes)
        ):
            raise ValueError("Session changes must have contiguous ascending sequences")
        if self.changes and self.changes[-1].sequence + 1 != self.next_sequence:
            raise ValueError("Session changes must end at their checkpoint's next sequence")

    @property
    def session_id(self) -> str:
        """The committing session; subagent sessions share their parent's handler."""
        return self.checkpoint.session_id

    @property
    def next_sequence(self) -> int:
        """The first log position not represented by this checkpoint."""
        return self.checkpoint.offset + len(self.checkpoint.events)


type CommitHandler = Callable[[SessionCommit], Awaitable[None]]
"""An application callback that stores one commit before returning."""


class CommitDelivery:
    """Deliver pending durable changes to an application handler at barriers.

    Input admission, model request and tool execution intent, tool results,
    turn completion, controls, approvals, plugin values, compaction and custom
    durable events are barriers: their append returns only after the handler
    returned. Other events ride along with the next barrier or explicit flush.
    A failing handler raises to the appending caller and rejects every later
    admission with the same error: the application's last stored commit stays
    the truth, and the conversation continues by reopening from it.

    The handler runs while the log holds its admission lock, so it must not
    write to or flush its own log. ``timeout_seconds`` bounds each delivery;
    an expired delivery fails like a raising handler, with ``CommitTimeout``.
    """

    def __init__(self, handler: CommitHandler, *, timeout_seconds: float | None = None) -> None:
        self.handler = handler
        self.timeout_seconds = timeout_seconds
        self._changes: list[SessionChange] = []
        self._closing = False
        self._failure: BaseException | None = None
        self._delivering: asyncio.Task[object] | None = None
        self._delivered: SessionState | None = None

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

    def record(self, change: SessionChange) -> bool:
        """Queue a change and report whether it must be delivered before admission returns."""
        self._changes.append(change)
        # Kernel-owned teardown can still remove named facts after the runtime closes.
        return self._closing or isinstance(change.event, _BARRIERS)

    async def deliver(self, capture: Callable[[], SessionCheckpoint]) -> None:
        """Hand pending changes to the application while the owning log holds its lock."""
        if not self._changes:
            return
        checkpoint = capture()
        commit = SessionCommit(
            checkpoint, tuple(self._changes), state_changed=self._state_changed(checkpoint.state)
        )
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
        self._delivered = checkpoint.state
        self._changes.clear()

    def close(self) -> None:
        """Deliver every later change immediately."""
        self._closing = True

    def _state_changed(self, state: SessionState) -> bool:
        # Retained entries and assets are shared objects, so equality is cheap.
        previous = self._delivered
        return previous is None or (previous is not state and previous != state)
