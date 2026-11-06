from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from harnessed.core.events.base import Identifier, SessionEvent
from harnessed.core.events.kinds import EventKind


class RuleAction(StrEnum):
    NOTE = "note"
    CONTEXT = "context"
    STEER = "steer"


@dataclass(frozen=True)
class RuleTriggeredEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.RULE_TRIGGERED] = field(default=EventKind.RULE_TRIGGERED, kw_only=True)
    rule: str
    call_id: Identifier
    occurrence: int
    actions: tuple[RuleAction, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "actions", tuple(self.actions))
