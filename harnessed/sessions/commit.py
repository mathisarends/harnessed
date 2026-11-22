from collections.abc import Awaitable, Callable
from itertools import pairwise
from typing import Annotated

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

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

from .checkpoint import CheckpointEntry, CheckpointExtension, SessionCheckpoint

_CONFIG = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
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


@dataclass(frozen=True, config=_CONFIG)
class SessionChange:
    """One new durable event at its conversation-local, zero-based log position."""

    __hash__ = None

    sequence: Annotated[int, Field(ge=0)]
    event: CheckpointEntry

    def __post_init__(self) -> None:
        if not isinstance(self.event, CheckpointExtension):
            validate_event(self.event)


@dataclass(frozen=True, config=_CONFIG)
class SessionCommit:
    """A session's complete checkpoint and the ordered changes since its last commit.

    Persist the checkpoint to reopen the session with ``Agent.session(state=...)``,
    and map the changes into application records in the same transaction.
    Sequences are stable, so applications can upsert redelivered changes. Neither
    value dictates the backend's schema.
    """

    __hash__ = None

    session_id: Annotated[str, Field(min_length=1)]
    checkpoint: SessionCheckpoint
    changes: tuple[SessionChange, ...] = ()

    def __post_init__(self) -> None:
        if any(
            following.sequence != previous.sequence + 1
            for previous, following in pairwise(self.changes)
        ):
            raise ValueError("Session changes must have contiguous ascending sequences")
        if self.changes and self.changes[-1].sequence + 1 != self.next_sequence:
            raise ValueError("Session changes must end at their checkpoint's next sequence")

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
    """

    def __init__(self, session_id: str, handler: CommitHandler) -> None:
        self.handler = handler
        self._session_id = session_id
        self._changes: list[SessionChange] = []
        self._closing = False
        self._failure: BaseException | None = None

    @property
    def failed(self) -> bool:
        """Whether a delivery raised; later admissions are rejected."""
        return self._failure is not None

    def check(self) -> None:
        """Reject admission after a failed delivery."""
        if self._failure is not None:
            raise self._failure

    def record(self, change: SessionChange) -> bool:
        """Queue a change and report whether it must be delivered before admission returns."""
        self._changes.append(change)
        # Kernel-owned teardown can still remove named facts after the runtime closes.
        return self._closing or isinstance(change.event, _BARRIERS)

    async def deliver(self, capture: Callable[[], SessionCheckpoint]) -> None:
        """Hand pending changes to the application while the owning log holds its lock."""
        if not self._changes:
            return
        commit = SessionCommit(self._session_id, capture(), tuple(self._changes))
        try:
            await self.handler(commit)
        except Exception as error:
            self._failure = error
            raise
        self._changes.clear()

    def close(self) -> None:
        """Deliver every later change immediately."""
        self._closing = True
