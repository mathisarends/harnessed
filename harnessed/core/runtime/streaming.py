from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from harnessed.core.events.approvals import (
    ApprovalNeededEvent,
)
from harnessed.core.events.base import CustomTransientEvent, DurableEvent
from harnessed.core.events.compaction import (
    CompactionAppliedEvent,
    CompactionFinishedEvent,
    CompactionStartedEvent,
)
from harnessed.core.events.completion import TurnFinishedEvent
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
    AgentControlChangedEvent,
    ControlState,
    SessionStatusChangedEvent,
)
from harnessed.core.events.stream import EventStream
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
    ToolProgressEvent,
)
from harnessed.core.events.turn import TurnStartedEvent
from harnessed.core.result import RunResult
from harnessed.core.runtime.control import SessionStatus

if TYPE_CHECKING:
    from harnessed.core.runtime.session import AgentEvent


async def project_stream_events(
    raw: AsyncGenerator[AgentEvent, None],
    *,
    completion: Callable[[], Awaitable[RunResult[Any]]],
) -> AsyncGenerator[EventStream[Any], None]:
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
                case DurableEvent() | CustomTransientEvent():
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
                case ToolProgressEvent(call_id=call_id) if call_id in calls:
                    yield event
                case ToolCallFinishedEvent(call_id=call_id) if call_id in calls:
                    yield event
                    calls.discard(call_id)
                case TurnStartedEvent() | AgentControlChangedEvent():
                    # A subsequent turn may already be running when this consumer resumes.
                    status = _stream_status(event)
                    if status != previous_status:
                        previous_status = status
                        yield SessionStatusChangedEvent(status)
        yield TurnFinishedEvent(await completion())
    finally:
        await raw.aclose()


def _stream_status(event: TurnStartedEvent | AgentControlChangedEvent) -> SessionStatus:
    match event:
        case AgentControlChangedEvent(state=ControlState.PAUSED):
            return SessionStatus.PAUSED
        case AgentControlChangedEvent(
            state=ControlState.STOPPING | ControlState.ABORTING | ControlState.STOPPED
        ):
            return SessionStatus.STOPPING
        case _:
            return SessionStatus.RUNNING
