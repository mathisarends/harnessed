from dataclasses import replace
from uuid import uuid4

from harnessed.core.errors import OutputTokenLimit
from harnessed.core.events.messages import AssistantMessageEvent
from harnessed.core.events.model import ModelUsageRecordedEvent
from harnessed.core.events.output import OutputValidatedEvent, OutputValidationFailedEvent
from harnessed.core.events.turn import (
    StepOutcome,
    TurnEndedEvent,
    TurnLimits,
    TurnOutcome,
    TurnStartedEvent,
)
from harnessed.core.hooks import TurnStopping
from harnessed.core.limits import FinalStepPolicy
from harnessed.core.model import GenerationSettings, StopReason, TokenLimitBehavior, sum_usage
from harnessed.core.runtime.context import SessionContext
from harnessed.core.runtime.control import SessionControl
from harnessed.core.runtime.options import AgentOptions
from harnessed.core.runtime.stepper import StepRunner
from harnessed.core.session.session import SessionLog
from harnessed.core.session.turns import Step, Turn
from harnessed.core.tracing import Span, span
from harnessed.kernel import Scope


class TurnRunner:
    def __init__(
        self,
        scope: Scope,
        log: SessionLog,
        options: AgentOptions,
        control: SessionControl,
        steps: StepRunner,
        context: SessionContext,
    ) -> None:
        self.scope = scope
        self.log = log
        self.options = options
        self.control = control
        self.steps = steps
        self.context = context
        self._trace: tuple[str, Span] | None = None

    def record_turn(self, turn: Turn) -> None:
        """Capture a turn before publication releases its execution buffers."""
        if self._trace is not None and self._trace[0] == turn.id:
            self._trace[1].set_attribute("turn", turn)

    async def run(self) -> bool:
        previous = self.log.open_turn
        turn = previous.id if previous is not None else uuid4().hex
        with span(
            "harnessed.turn", scope=self.scope, session_id=self.log.id, turn_id=turn
        ) as current:
            self._trace = (turn, current)
            try:
                return await self._run(turn)
            finally:
                try:
                    opened = self.log.open_turn
                    if opened is not None:
                        self.record_turn(opened)
                finally:
                    self._trace = None

    async def _run(self, turn: str) -> bool:
        previous = self.log.open_turn
        first_step = len(previous.steps) + 1 if previous is not None else 1
        if previous is None:
            candidates = self.log.inbox.next_turn or self.log.inbox.next_step
            first = candidates[0] if candidates else None
            limits = first.limits if first is not None else TurnLimits()
            start = TurnStartedEvent(
                turn,
                first.message.id if first is not None else None,
                first.settings if first is not None else GenerationSettings(),
                output_schema=first.output_schema if first is not None else None,
                limits=replace(limits, max_steps=limits.max_steps or self.options.max_steps),
                final_step=self.options.final_step,
            )
            await self.log.append(start)
        else:
            start = previous.start
            if (
                previous.steps
                and start.final_step is FinalStepPolicy.SUMMARIZE
                and previous.steps[-1].number >= (start.limits.max_steps or self.options.max_steps)
            ):
                await self.log.append(TurnEndedEvent(turn, TurnOutcome.MAX_STEPS))
                return True
            if previous.steps and not await self._continue_from(previous.steps[-1]):
                await self.log.append(TurnEndedEvent(turn))
                return True
        settings = start.settings
        max_steps = start.limits.max_steps or self.options.max_steps
        budget = start.limits.token_budget
        for step in range(first_step, max_steps + 1):
            if budget is not None and self._spent_tokens() >= budget:
                await self.log.append(TurnEndedEvent(turn, TurnOutcome.TOKEN_BUDGET))
                return True
            final = step == max_steps and start.final_step is FinalStepPolicy.SUMMARIZE
            with span(
                "harnessed.step", scope=self.scope, session_id=self.log.id, turn_id=turn, step=step
            ):
                if not await self.steps.run(turn, step, settings, max_steps=max_steps, final=final):
                    return True
            await self.control.checkpoint()
            if final:
                await self.log.append(TurnEndedEvent(turn, TurnOutcome.MAX_STEPS))
                return True
            if await self._continue_from(self.log.current_turn().steps[-1]):
                continue
            await self.log.append(TurnEndedEvent(turn))
            return True
        await self.log.append(TurnEndedEvent(turn, TurnOutcome.MAX_STEPS))
        return True

    def _spent_tokens(self) -> int:
        """Input and output tokens billed so far in the open turn, compaction included."""
        events = self.log.current_turn().events
        usage = sum_usage(e.usage for e in events if isinstance(e, ModelUsageRecordedEvent))
        return usage.total_tokens

    async def _continue_from(self, step: Step) -> bool:
        if step.reason == StepOutcome.CONTEXT_LIMIT:
            return True
        response = next(
            (event for event in reversed(step.events) if isinstance(event, AssistantMessageEvent)),
            None,
        )
        if response is not None and response.stop_reason == StopReason.MAX_TOKENS:
            if self.options.on_max_tokens == TokenLimitBehavior.FAIL:
                raise OutputTokenLimit("The model reached its output token limit")
            if self.options.on_max_tokens == TokenLimitBehavior.CONTINUE:
                return True
        verdicts = [
            e
            for e in step.events
            if isinstance(e, OutputValidatedEvent | OutputValidationFailedEvent)
        ]
        output_calls = {e.call_id for e in verdicts}
        invalid = any(isinstance(e, OutputValidationFailedEvent) for e in verdicts)
        if (
            step.reason == StepOutcome.INTERRUPTED
            or response is None
            or invalid
            or any(call.id not in output_calls for call in response.message.tool_calls)
            or self.log.has_step_wakeup
        ):
            return True
        continuation = await self.scope.dispatch(TurnStopping(self.context))
        return bool(continuation or self.log.has_step_wakeup)
