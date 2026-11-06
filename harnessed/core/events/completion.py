from dataclasses import dataclass, field
from typing import Literal, final

from harnessed.core.events.base import TransientEvent
from harnessed.core.events.kinds import EventKind
from harnessed.core.result import RunResult


@final
@dataclass(frozen=True, slots=True)
class TurnResultEvent[T](TransientEvent):
    """End a normally exhausted UI stream with its assembled run result.

    Stream errors or consumer cancellation can prevent this notification.
    """

    __hash__ = None
    type: Literal[EventKind.TURN_RESULT] = field(default=EventKind.TURN_RESULT, kw_only=True)
    result: RunResult[T]
