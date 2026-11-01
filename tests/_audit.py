from collections.abc import Iterable

from harnessed import Session
from harnessed.core.approvals import ApprovalState
from harnessed.core.event_registry import EventRegistry
from harnessed.core.hooks import SessionAppended
from harnessed.core.model import ModelRequest, ModelUsage, sum_usage
from harnessed.core.session.control import RunControl
from harnessed.core.session.events import (
    ModelUsageRecordedEvent,
    SessionEvent,
    StepEndedEvent,
    StepStartedEvent,
    TurnEndedEvent,
    TurnStartedEvent,
)
from harnessed.core.session.history import derive_messages, derive_requests
from harnessed.core.session.inbox import Inbox
from harnessed.core.session.turns import Step, Turn


class Audit:
    """Test-owned capture; the runtime deliberately does not archive old turns."""

    def __init__(self, session: Session) -> None:
        self._events: list[SessionEvent] = list(session.events)
        session.on(SessionAppended, self._record)

    def _record(self, event: SessionAppended) -> None:
        self._events.append(event.event)

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        return tuple(self._events)

    @property
    def turns(self) -> tuple[Turn, ...]:
        return derive_turns(self.events)

    @property
    def requests(self) -> tuple[ModelRequest, ...]:
        return derive_requests(self.events)


class AuditProjection:
    """Read exported audit records without creating or restoring a runtime."""

    def __init__(self, events: Iterable[SessionEvent]) -> None:
        self.events = tuple(events)
        registry = EventRegistry()
        for event in self.events:
            registry.validate(event)
        self.approvals = ApprovalState(self.events)
        self.turns = derive_turns(self.events)
        self.inbox = Inbox(self.events)
        self.control = RunControl(self.events)
        self.messages = derive_messages(self.events)
        self.requests = derive_requests(self.events)
        self.usage: ModelUsage = sum_usage(
            event.usage for event in self.events if isinstance(event, ModelUsageRecordedEvent)
        )


def derive_turns(events: Iterable[SessionEvent]) -> tuple[Turn, ...]:
    events = tuple(events)
    turns, steps = [], []
    turn_start = step_start = None
    turn_id = None
    step_number = 0
    for seq, event in enumerate(events):
        if isinstance(event, TurnStartedEvent):
            if turn_start is not None:
                raise ValueError("A turn cannot start inside an open turn")
            turn_start, turn_id, steps = seq, event.turn_id, []
        elif isinstance(event, StepStartedEvent):
            if turn_start is None or event.turn_id != turn_id or step_start is not None:
                raise ValueError("A step must start inside its turn, after the previous step")
            if type(event.step) is not int or event.step != len(steps) + 1:
                raise ValueError("Steps must be numbered consecutively from one within each turn")
            step_start, step_number = seq, event.step
        elif isinstance(event, StepEndedEvent):
            if step_start is None or (event.turn_id, event.step) != (turn_id, step_number):
                raise ValueError("StepEndedEvent must close the current step")
            assert turn_id is not None
            steps.append(Step(turn_id, step_number, events[step_start : seq + 1], event.reason))
            step_start = None
        elif isinstance(event, TurnEndedEvent):
            if turn_start is None or event.turn_id != turn_id or step_start is not None:
                raise ValueError("TurnEndedEvent must close the current turn after its last step")
            assert turn_id is not None
            turns.append(Turn(turn_id, events[turn_start : seq + 1], tuple(steps), event.reason))
            turn_start, turn_id = None, None
    if turn_start is not None:
        assert turn_id is not None
        if step_start is not None:
            steps.append(Step(turn_id, step_number, events[step_start:]))
        turns.append(Turn(turn_id, events[turn_start:], tuple(steps)))
    return tuple(turns)
