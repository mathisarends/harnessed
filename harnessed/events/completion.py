from dataclasses import dataclass, field
from typing import Literal, final

from harnessed.sessions.result import RunResult

from .base import TransientEvent
from .kinds import EventKind


@final
@dataclass(frozen=True, slots=True)
class TurnResultEvent[T](TransientEvent[T]):
    """End a normally exhausted UI stream with its assembled run result.

    Stream errors or consumer cancellation can prevent this notification.
    """

    __hash__ = None
    type: Literal[EventKind.TURN_RESULT] = field(default=EventKind.TURN_RESULT, kw_only=True)
    result: RunResult[T]
