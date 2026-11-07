from dataclasses import dataclass, field
from enum import StrEnum
from time import time
from typing import Annotated, Literal, Self

from pydantic import Field

from harnessed.core.events.base import DurableEvent, Identifier
from harnessed.core.events.kinds import EventKind
from harnessed.core.events.turn import TurnLimits
from harnessed.core.messages import UserMessage
from harnessed.core.model import GenerationSettings, OutputSchema


class InboxMode(StrEnum):
    FOLLOWUP = "followup"
    STEER = "steer"


class InboxTarget(StrEnum):
    NEXT_TURN = "next_turn"
    NEXT_STEP = "next_step"


class InboxRemovalReason(StrEnum):
    CLAIMED = "claimed"
    CANCELED = "canceled"
    STOPPED = "stopped"
    DISCARDED = "discarded"
    REJECTED = "rejected"


_INBOX_ROUTES: dict[InboxMode, InboxTarget] = {
    InboxMode.FOLLOWUP: InboxTarget.NEXT_TURN,
    InboxMode.STEER: InboxTarget.NEXT_STEP,
}


@dataclass(frozen=True)
class InboxAddedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.INBOX_ADDED] = field(default=EventKind.INBOX_ADDED, kw_only=True)
    message: UserMessage
    target: InboxTarget
    mode: InboxMode
    settings: GenerationSettings = field(default_factory=GenerationSettings)
    created_at: Annotated[float, Field(ge=0)] = field(default=0.0, kw_only=True)
    output_schema: OutputSchema | None = field(default=None, kw_only=True)
    limits: TurnLimits = field(default_factory=TurnLimits, kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", InboxMode(self.mode))
        object.__setattr__(self, "target", InboxTarget(self.target))
        if self.mode not in _INBOX_ROUTES:
            raise ValueError(f"Unknown inbox mode: {self.mode!r}")
        if self.target != _INBOX_ROUTES[self.mode]:
            raise ValueError(f"Inbox routing does not match {self.mode!r}")
        if self.message.role != "user":
            raise ValueError(f"{self.mode} requires a user message")
        if self.output_schema is not None and self.mode != InboxMode.FOLLOWUP:
            raise ValueError("Only a new turn can declare an output contract")
        if self.limits != TurnLimits() and self.mode != InboxMode.FOLLOWUP:
            raise ValueError("Only a new turn can declare limits")
        if not isinstance(self.settings, GenerationSettings):
            raise TypeError("Inbox settings must be GenerationSettings")

    @classmethod
    def create(
        cls,
        message: UserMessage,
        mode: InboxMode,
        *,
        settings: GenerationSettings | None = None,
        output_schema: OutputSchema | None = None,
        limits: TurnLimits | None = None,
    ) -> Self:
        if mode not in _INBOX_ROUTES:
            raise ValueError(f"Unknown inbox mode: {mode!r}")
        return cls(
            message,
            _INBOX_ROUTES[mode],
            mode,
            settings if settings is not None else GenerationSettings(),
            created_at=time(),
            output_schema=output_schema,
            limits=limits if limits is not None else TurnLimits(),
        )


@dataclass(frozen=True)
class InboxRemovedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.INBOX_REMOVED] = field(default=EventKind.INBOX_REMOVED, kw_only=True)
    message_id: Identifier
    reason: InboxRemovalReason = InboxRemovalReason.CLAIMED
