from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from harnessed.core.events.base import SessionEvent, TransientEvent
from harnessed.core.events.kinds import EventKind


class SessionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    CLOSED = "closed"


class ControlState(StrEnum):
    ACTIVE = "active"
    PAUSING = "pausing"
    PAUSED = "paused"
    STOPPING = "stopping"
    ABORTING = "aborting"
    STOPPED = "stopped"


@dataclass(frozen=True)
class AgentControlChangedEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.AGENT_CONTROL_CHANGED] = field(
        default=EventKind.AGENT_CONTROL_CHANGED, kw_only=True
    )
    state: ControlState
    keep_inbox: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", ControlState(self.state))
        if type(self.keep_inbox) is not bool or (
            self.keep_inbox
            and self.state
            not in (ControlState.STOPPING, ControlState.ABORTING, ControlState.STOPPED)
        ):
            raise ValueError("keep_inbox applies only to stop or abort requests")


@dataclass(frozen=True, slots=True)
class SessionStatusChangedEvent(TransientEvent):
    type: Literal[EventKind.STATUS_CHANGED] = field(default=EventKind.STATUS_CHANGED, kw_only=True)
    status: SessionStatus
