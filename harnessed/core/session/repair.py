from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Self

from harnessed.core.events.base import DurableEvent
from harnessed.core.events.messages import AssistantMessageEvent
from harnessed.core.events.session import ControlState
from harnessed.core.events.tools import (
    ToolCallFinishedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
)
from harnessed.core.events.turn import (
    StepEndedEvent,
    StepOutcome,
    StepStartedEvent,
    TurnEndedEvent,
    TurnOutcome,
    TurnStartedEvent,
)
from harnessed.core.messages import ToolCall, ToolResult, ToolStatus
from harnessed.core.session.control import RunControl


@dataclass
class InterruptedActivity:
    """Tool, step and turn activity a cancelled or resumed execution left open."""

    turn: str | None = None
    step: int | None = None
    unanswered: dict[str, ToolCall] = field(default_factory=dict)
    started: set[str] = field(default_factory=set)
    executed: set[str] = field(default_factory=set)
    cancelled: set[str] = field(default_factory=set)
    batches: dict[str, str] = field(default_factory=dict)
    broken_batches: set[str] = field(default_factory=set)

    @classmethod
    def scan(cls, events: Iterable[DurableEvent]) -> Self:
        activity = cls()
        for event in events:
            activity._observe(event)
        return activity

    def repairs(self, control: RunControl) -> tuple[DurableEvent, ...]:
        """Return the records that answer every open call and close the open turn."""
        records: list[DurableEvent] = [
            ToolExecutionFinishedEvent(
                call_id,
                ToolResult(
                    "Tool execution interrupted; external outcome is unknown",
                    ToolStatus.CANCELLED,
                ),
            )
            for call_id in sorted(self.started - self.executed)
        ]
        records.extend(
            ToolCallFinishedEvent(call, self._interrupted_result(call, control))
            for call in self.unanswered.values()
        )
        if self.turn is None:
            return tuple(records)
        if self.step is not None:
            outcome = StepOutcome.INTERRUPTED if control.paused else StepOutcome.ABORTED
            records.append(StepEndedEvent(self.turn, self.step, outcome))
        if not control.paused:
            stopping = control.state in (ControlState.STOPPING, ControlState.STOPPED)
            reason = TurnOutcome.STOPPED if stopping and self.step is None else TurnOutcome.ABORTED
            records.append(TurnEndedEvent(self.turn, reason))
        return tuple(records)

    def _observe(self, event: DurableEvent) -> None:
        match event:
            case TurnStartedEvent(turn_id=turn_id):
                self.turn = turn_id
            case StepStartedEvent(step=index):
                self.step = index
            case AssistantMessageEvent(message=message):
                for call in message.tool_calls:
                    self.batches[call.id] = message.id
                    self.unanswered[call.id] = call
                    self.started.discard(call.id)
                    self.executed.discard(call.id)
            case ToolCallFinishedEvent(call_id=call_id, result=result):
                self._break_batch(call_id, result)
                self.unanswered.pop(call_id, None)
            case ToolExecutionStartedEvent(call_id=call_id):
                self.started.add(call_id)
            case ToolExecutionFinishedEvent(call_id=call_id, result=result):
                self.executed.add(call_id)
                self._break_batch(call_id, result)
                if result.status is ToolStatus.CANCELLED:
                    self.cancelled.add(call_id)
            case StepEndedEvent():
                self.step = None
            case TurnEndedEvent():
                self.turn = None

    def _break_batch(self, call_id: str, result: ToolResult) -> None:
        if result.break_chain and call_id in self.batches:
            self.broken_batches.add(self.batches[call_id])

    def _interrupted_result(self, call: ToolCall, control: RunControl) -> ToolResult:
        if call.id not in self.started and self.batches.get(call.id) in self.broken_batches:
            return ToolResult(
                "Skipped because an earlier tool ended this action chain", ToolStatus.SKIPPED
            )
        if call.id in self.executed and call.id not in self.cancelled:
            return ToolResult.error(
                "Tool execution finished, but result review was interrupted. "
                "The raw result is retained in tool.execution.finished for inspection. "
                "Do not repeat the action solely to recover its result."
            )
        message = "Tool execution interrupted" if control.paused else "Tool execution aborted"
        if call.id in self.started:
            message += "; external outcome is unknown. Inspect external state before retrying."
        status = ToolStatus.CANCELLED if call.id in self.started else ToolStatus.ERROR
        return ToolResult(message, status)
