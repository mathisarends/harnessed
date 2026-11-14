from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from harnessed.core.buffering import EventBuffer
from harnessed.core.errors import SessionClosed
from harnessed.core.events.approvals import (
    ApprovalNeededEvent,
)
from harnessed.core.events.base import DurableEvent, TransientEvent
from harnessed.core.events.compaction import (
    CompactionAppliedEvent,
    CompactionFinishedEvent,
    CompactionStartedEvent,
)
from harnessed.core.events.completion import TurnResultEvent
from harnessed.core.events.inbox import InboxAddedEvent, InboxRemovedEvent
from harnessed.core.events.messages import ReasoningDeltaEvent, TextDeltaEvent
from harnessed.core.events.model import (
    ModelAttemptFinishedEvent,
    ModelAttemptStartedEvent,
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelRetryScheduledEvent,
)
from harnessed.core.events.observations import SpillCreatedEvent, SpillExpiredEvent
from harnessed.core.events.output import OutputValidatedEvent, OutputValidationFailedEvent
from harnessed.core.events.session import (
    ControlState,
    SessionControlChangedEvent,
    SessionStatusChangedEvent,
)
from harnessed.core.events.stream import STREAM_EVENT_TYPES, StreamItem, coalesce_deltas
from harnessed.core.events.subagents import (
    SubagentApprovalRequestedEvent,
    SubagentFinishedEvent,
    SubagentLinkedEvent,
    SubagentMessageSentEvent,
    SubagentStartedEvent,
    SubagentUpdatedEvent,
)
from harnessed.core.events.tools import (
    ToolArgumentsDeltaEvent,
    ToolCallFinishedEvent,
    ToolCallGenerationStartedEvent,
    ToolCallRequestedEvent,
    ToolExecutionStartedEvent,
    ToolProgressEvent,
)
from harnessed.core.events.turn import TurnEndedEvent, TurnStartedEvent
from harnessed.core.events.unions import SESSION_EVENT_TYPES
from harnessed.core.result import RunResult
from harnessed.core.runtime.control import SessionControl, SessionStatus
from harnessed.core.runtime.inbox import SessionInbox


async def project_stream_events(
    raw: AsyncGenerator[DurableEvent | TransientEvent, None],
    *,
    completion: Callable[[], Awaitable[RunResult[Any]]],
) -> AsyncGenerator[StreamItem[Any], None]:
    """Project UI events, yielding one terminal result after normal exhaustion."""
    calls: set[str] = set()
    control_calls: set[str] = set()
    previous_status: SessionStatus | None = None
    try:
        async for event in raw:
            match event:
                case (
                    OutputValidatedEvent(call_id=call_id)
                    | OutputValidationFailedEvent(call_id=call_id)
                ):
                    if call_id is not None:
                        control_calls.add(call_id)
                case (
                    TextDeltaEvent()
                    | ReasoningDeltaEvent()
                    | ApprovalNeededEvent()
                    | ToolCallGenerationStartedEvent()
                    | ToolArgumentsDeltaEvent()
                    | CompactionStartedEvent()
                    | CompactionFinishedEvent()
                    | ModelAttemptStartedEvent()
                    | ModelAttemptFinishedEvent()
                    | ModelRetryScheduledEvent()
                    | ModelRequestedEvent()
                    | ModelRequestFailedEvent()
                ):
                    yield event
                case CompactionAppliedEvent() | SpillCreatedEvent() | SpillExpiredEvent():
                    yield event
                case DurableEvent() | TransientEvent() if (
                    event.type not in SESSION_EVENT_TYPES and event.type not in STREAM_EVENT_TYPES
                ):
                    yield event
                case (
                    SubagentLinkedEvent()
                    | SubagentStartedEvent()
                    | SubagentFinishedEvent()
                    | SubagentApprovalRequestedEvent()
                    | SubagentUpdatedEvent()
                    | SubagentMessageSentEvent()
                ):
                    yield event
                case ToolCallRequestedEvent(call=call) if call.id not in control_calls:
                    calls.add(call.id)
                    yield event
                case (
                    ToolExecutionStartedEvent(call_id=call_id) | ToolProgressEvent(call_id=call_id)
                ) if call_id in calls:
                    yield event
                case ToolCallFinishedEvent(call_id=call_id) if call_id in calls:
                    yield event
                    calls.discard(call_id)
                case TurnStartedEvent() | SessionControlChangedEvent():
                    # A subsequent turn may already be running when this consumer resumes.
                    status = _stream_status(event)
                    if status != previous_status:
                        previous_status = status
                        yield SessionStatusChangedEvent(status)
        result = await completion()
        yield TurnResultEvent(result)
    finally:
        await raw.aclose()


class TurnStreams:
    """Fan session events out to open stream() buffers and follow one run per buffer.

    A buffer that overflows is detached; the run continues without it.
    """

    def __init__(self, control: SessionControl, inbox: SessionInbox) -> None:
        self._control = control
        self._inbox = inbox
        self._queues: set[EventBuffer[DurableEvent | TransientEvent | None]] = set()

    def __len__(self) -> int:
        return len(self._queues)

    def attach(self, buffer_size: int) -> EventBuffer[DurableEvent | TransientEvent | None]:
        queue: EventBuffer[DurableEvent | TransientEvent | None] = EventBuffer(
            buffer_size, merge=coalesce_deltas
        )
        self._queues.add(queue)
        return queue

    def detach(self, queue: EventBuffer[DurableEvent | TransientEvent | None]) -> None:
        self._queues.discard(queue)

    def publish(self, event: DurableEvent | TransientEvent | None) -> None:
        """Deliver an event, or None when the driver stops, to every attached buffer."""
        for queue in tuple(self._queues):
            if not queue.put(event):
                self._queues.discard(queue)

    async def follow(
        self, queue: EventBuffer[DurableEvent | TransientEvent | None], input_id: str
    ) -> AsyncGenerator[DurableEvent | TransientEvent, None]:
        """Yield one run's records from its admission until its turn ends or is discarded."""
        try:
            turn_id = None
            while True:
                event = await queue.get()
                if event is None:
                    if self._control.error is not None:
                        raise self._control.error
                    if self._control.closing or self._control.status is SessionStatus.CLOSED:
                        raise SessionClosed(
                            "This handle closed before the turn finished; "
                            "use an earlier conversation snapshot to continue"
                        )
                    if any(item.id == input_id for item in self._inbox.pending):
                        self._control.wake()
                        continue
                    return
                if isinstance(event, TurnStartedEvent):
                    if event.input_id == input_id:
                        turn_id = event.turn_id
                if turn_id is None:
                    if isinstance(event, InboxAddedEvent) and event.message.id == input_id:
                        yield event
                    if isinstance(event, InboxRemovedEvent) and event.message_id == input_id:
                        yield event
                        return
                    continue
                yield event
                if isinstance(event, TurnEndedEvent) and event.turn_id == turn_id:
                    if self._control.error is not None:
                        raise self._control.error
                    return
        finally:
            self._queues.discard(queue)


def _stream_status(event: TurnStartedEvent | SessionControlChangedEvent) -> SessionStatus:
    match event:
        case SessionControlChangedEvent(state=ControlState.PAUSED):
            return SessionStatus.PAUSED
        case SessionControlChangedEvent(
            state=ControlState.STOPPING | ControlState.ABORTING | ControlState.STOPPED
        ):
            return SessionStatus.STOPPING
        case _:
            return SessionStatus.RUNNING
