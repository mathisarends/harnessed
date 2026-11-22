from dataclasses import field
from enum import StrEnum
from typing import Literal

from pydantic.dataclasses import dataclass

from harnessed.models.values import VALUE_CONFIG

from .base import DurableEvent, TransientEvent
from .kinds import EventKind


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


@dataclass(frozen=True, config=VALUE_CONFIG)
class SessionControlChangedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.SESSION_CONTROL_CHANGED] = field(
        default=EventKind.SESSION_CONTROL_CHANGED, kw_only=True
    )
    state: ControlState
    keep_inbox: bool = False

    def __post_init__(self) -> None:
        if self.keep_inbox and self.state not in (
            ControlState.STOPPING,
            ControlState.ABORTING,
            ControlState.STOPPED,
        ):
            raise ValueError("keep_inbox applies only to stop or abort requests")


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class SessionStatusChangedEvent(TransientEvent):
    type: Literal[EventKind.SESSION_STATUS_CHANGED] = field(
        default=EventKind.SESSION_STATUS_CHANGED, kw_only=True
    )
    status: SessionStatus
