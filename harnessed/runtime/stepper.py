import asyncio
from dataclasses import replace
from uuid import uuid4

from harnessed.errors import ContextLengthExceeded, OutputValidationError
from harnessed.events import (
    AssistantMessageEvent,
    InboxAddedEvent,
    InboxRemovalReason,
    InboxRemovedEvent,
    MessageAppendedEvent,
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelRequestOutcome,
    ModelUsageRecordedEvent,
    ObservationRecordedEvent,
    ObservationsIncludedEvent,
    OutputValidationFailedEvent,
    StepEndedEvent,
    StepOutcome,
    StepStartedEvent,
    SystemMessageEvent,
    TurnEndedEvent,
    TurnOutcome,
    UserMessageEvent,
)
from harnessed.events.turn import FinalStepPolicy
from harnessed.kernel import Scope
from harnessed.models import (
    AssistantMessage,
    GenerationSettings,
    Message,
    Model,
    ModelAttemptOutcome,
    ModelRequest,
    RetryKind,
    Role,
    SystemMessage,
    UserMessage,
)
from harnessed.models.model import resolve_request_settings
from harnessed.prompts import SystemPromptStore
from harnessed.sessions.context import PendingObservations, SessionContext
from harnessed.sessions.log import SessionLog
from harnessed.tools import ToolRegistry

from .budget import ContextBudget
from .compaction import CompactionManager, CompactionPolicy
from .control import SessionControl
from .executor import ToolExecutor
from .hooks import (
    OutputDecision,
    PrepareOutput,
    PrepareRequest,
    PreStep,
    StepDecision,
    TurnProgress,
)
from .options import AgentOptions
from .output import OutputHandler
from .respond import ModelResponder
from .retries import RequestRetries
from .tokens import TokenCounter


class StepRunner:
    def __init__(
        self,
        scope: Scope,
        log: SessionLog,
        options: AgentOptions,
        control: SessionControl,
        compaction: CompactionManager,
        outputs: OutputHandler,
        tools: ToolExecutor,
        responder: ModelResponder,
        context: SessionContext,
        observations: PendingObservations,
    ) -> None:
        self.scope = scope
        self.log = log
        self.options = options
        self.control = control
        self._compaction = compaction
        self.outputs = outputs
        self.tools = tools
        self._responder = responder
        self.context = context
        self.observations = observations
        self._retries = RequestRetries(log, options)

    async def run(self, progress: TurnProgress, settings: GenerationSettings) -> bool:
        await self.control.checkpoint()
        await self.scope.settle()
        if not await self._start_step(progress):
            return False
        turn, step, max_steps = progress.turn_id, progress.step, progress.max_steps
        final = progress.is_final_step
        model = self.scope[Model]
        request = await self._prepare_request(settings, model=model, progress=progress)
        request_id = uuid4().hex
        record = self._retries.request(request, request_id, model.info)
        try:
            response = await self._request(record, model)
        except ContextLengthExceeded as exceeded:
            await self._recover_context(
                record, request, exceeded, model=model, turn=turn, step=step, max_steps=max_steps
            )
            return True
        except BaseException as error:
            await self._retries.finished(record, _failure_outcome(error))
            raise
        await self._finish_step(
            record, response, request, turn=turn, step=step, max_steps=max_steps, final=final
        )
        return True

    async def _start_step(self, progress: TurnProgress) -> bool:
        """Apply PreStep to pending input and record the accepted step input."""
        turn, step = progress.turn_id, progress.step
        pending = self.log.inbox.for_step(step)
        incoming = tuple(item.message for item in pending)
        decision = await self.scope.dispatch(
            PreStep(self.context, incoming, progress),
            default=lambda incoming=incoming: StepDecision(incoming),
        )
        if not decision.accepted:
            for item in pending:
                await self.log.append(
                    InboxRemovedEvent(item.message.id, InboxRemovalReason.REJECTED)
                )
            await self.log.append(TurnEndedEvent(turn, TurnOutcome.REJECTED))
            return False
        await self.log.append(StepStartedEvent(turn, step))
        prompt = await self.scope[SystemPromptStore].render()
        previous_event = self.log.last(SystemMessageEvent)
        previous = previous_event.content if previous_event is not None else None
        if prompt != previous:
            await self.log.append(SystemMessageEvent(prompt))
        await self._record_input(pending, decision.messages)
        observations = self.observations.drain()
        for source, message, refs in observations:
            await self.log.append(ObservationRecordedEvent(message, source, turn, step, refs))
        return True

    async def _record_input(
        self, pending: tuple[InboxAddedEvent, ...], accepted: tuple[Message, ...]
    ) -> None:
        accepted_ids = {message.id for message in accepted}
        if len(accepted_ids) != len(accepted) or any(
            message.role not in (Role.USER, Role.SYSTEM) for message in accepted
        ):
            raise ValueError("PreStep must return unique user or system messages")
        for item in pending:
            if item.message.id not in accepted_ids:
                await self.log.append(InboxRemovedEvent(item.message.id))
        for message in accepted:
            event = (
                UserMessageEvent(message)
                if isinstance(message, UserMessage)
                else MessageAppendedEvent(message)
            )
            await self.log.append(event)

    async def _prepare_request(
        self, settings: GenerationSettings, *, model: Model, progress: TurnProgress
    ) -> ModelRequest:
        """Build the step's model request through hooks, output policy and compaction."""
        request = ModelRequest(
            self.log.messages,
            self.scope[ToolRegistry].visible_definitions(self.scope),
            settings=settings,
        )
        request = await self.scope.dispatch(
            PrepareRequest(self.context, request, progress),
            default=lambda request=request: request,
        )
        final = progress.is_final_step
        if final:
            request = replace(
                request,
                tools=(),
                output_schema=None,
                messages=(*request.messages, SystemMessage(_FINAL_STEP_INSTRUCTION)),
            )
        else:
            request = await self._prepare_output(request, model)
        request = resolve_request_settings(request, model.settings)
        request = await self.log.retain_request(request)
        request = await self._compaction.prepare(request, model=model)
        if final:
            request = replace(request, tools=(), output_schema=None)
        if ContextBudget in self.scope:
            await self.scope[ContextBudget].check(
                request,
                context_window=model.info.context_window,
                counter=self.scope[TokenCounter],
            )
        return request

    async def _prepare_output(self, request: ModelRequest, model: Model) -> ModelRequest:
        schema = self.log.current_turn().start.output_schema
        output_decision = await self.scope.dispatch(
            PrepareOutput(self.context, request, schema),
            default=lambda: OutputDecision.ENFORCE,
        )
        if not isinstance(output_decision, OutputDecision):
            raise TypeError("PrepareOutput must return an OutputDecision")
        return self.outputs.prepare(
            request, schema, model=model, enforce=output_decision is OutputDecision.ENFORCE
        )

    async def _recover_context(
        self,
        record: ModelRequestedEvent,
        request: ModelRequest,
        exceeded: ContextLengthExceeded,
        *,
        model: Model,
        turn: str,
        step: int,
        max_steps: int,
    ) -> None:
        """Compact once and schedule a new step, or re-raise the context overflow."""
        current = self.log.current_turn()
        if (
            CompactionPolicy not in self.scope
            or step >= max_steps
            or any(item.reason == StepOutcome.CONTEXT_LIMIT for item in current.steps)
            or any(isinstance(event, AssistantMessageEvent) for event in current.steps[-1].events)
        ):
            await self._retries.finished(record, ModelAttemptOutcome.EXHAUSTED)
            raise exceeded
        try:
            applied, _ = await self._compaction.compact(request, model=model)
        except BaseException as error:
            await self._retries.finished(record, _failure_outcome(error))
            raise
        if applied is None:
            await self._retries.finished(record, ModelAttemptOutcome.FAILED)
            raise exceeded
        # A retry is a new model step. Completed tools and visible partial replies
        # are never replayed by retrying an in-progress step.
        await self.log.append(StepEndedEvent(turn, step, StepOutcome.CONTEXT_LIMIT))
        if record.retry_kind == RetryKind.OUTPUT_CORRECTION:
            await self._retries.finished(record, ModelAttemptOutcome.FAILED)
        await self._retries.scheduled(record, RetryKind.CONTEXT_RECOVERY)

    async def _finish_step(
        self,
        record: ModelRequestedEvent,
        response: AssistantMessage,
        request: ModelRequest,
        *,
        turn: str,
        step: int,
        max_steps: int,
        final: bool,
    ) -> None:
        """Validate output, run tools, close the step and settle output corrections."""
        try:
            results, failure = await self.outputs.validate(response, request.output_schema)
            await self.tools.run(response.tool_calls, results=results, allow_execution=not final)
        except BaseException as error:
            await self._retries.finished(record, _failure_outcome(error))
            raise
        await self.log.append(StepEndedEvent(turn, step))
        invalid = any(
            isinstance(event, OutputValidationFailedEvent) and event.message_id == response.id
            for event in self.log.current_turn().steps[-1].events
        )
        if invalid and failure is None and self._may_correct_output(step, max_steps=max_steps):
            if record.retry_kind == RetryKind.CONTEXT_RECOVERY:
                await self._retries.finished(record, ModelAttemptOutcome.SUCCEEDED)
            await self._retries.scheduled(record, RetryKind.OUTPUT_CORRECTION)
        else:
            await self._retries.finished(
                record,
                ModelAttemptOutcome.EXHAUSTED if invalid else ModelAttemptOutcome.SUCCEEDED,
                kind=RetryKind.OUTPUT_CORRECTION if invalid else None,
            )
        if failure is not None:
            raise OutputValidationError(f"Structured output retry limit reached: {failure}")

    def _may_correct_output(self, step: int, *, max_steps: int) -> bool:
        """Whether another step and the token budget remain for an output correction."""
        turn = self.log.current_turn()
        limits = turn.start.limits
        next_is_final = step + 1 == max_steps and turn.start.final_step is FinalStepPolicy.SUMMARIZE
        return (
            step < max_steps
            and not next_is_final
            and (
                limits.token_budget is None
                or sum(
                    event.usage.total_tokens
                    for event in turn.events
                    if isinstance(event, ModelUsageRecordedEvent)
                )
                < limits.token_budget
            )
        )

    async def _request(self, record: ModelRequestedEvent, model: Model) -> AssistantMessage:
        request = record.request
        request_id = record.request_id
        try:
            included = self.log.included_observations(request)
            await self.log.append(record)
            await self.log.append(ObservationsIncludedEvent(request_id, included))
            await self._retries.started(record)
            return await self._responder.respond(request, model=model, request_id=request_id)
        except BaseException as error:
            if self.log.request(request_id) is not None and not self.log.request_finished(
                request_id
            ):
                match error:
                    case asyncio.CancelledError():
                        outcome = ModelRequestOutcome.CANCELLED
                    case ContextLengthExceeded():
                        outcome = ModelRequestOutcome.CONTEXT_EXCEEDED
                    case _:
                        outcome = ModelRequestOutcome.FAILED
                await self.log.append(ModelRequestFailedEvent(request_id, outcome, str(error)))
            raise


_FINAL_STEP_INSTRUCTION = (
    "This is the final available step. End this turn with a text summary of the results, "
    "uncertainties and unfinished work based on the information already available. "
    "Do not call tools or claim unfinished work is complete."
)


def _failure_outcome(error: BaseException) -> ModelAttemptOutcome:
    if isinstance(error, asyncio.CancelledError):
        return ModelAttemptOutcome.CANCELLED
    return ModelAttemptOutcome.FAILED
