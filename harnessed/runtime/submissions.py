from collections.abc import Callable, Iterable
from typing import Any, overload

from harnessed.errors import RunUnavailable, SessionClosed, SessionStateError
from harnessed.events import (
    DurableEvent,
    InboxAddedEvent,
    InboxMode,
    InboxRemovalReason,
    InboxRemovedEvent,
    TurnEndedEvent,
    TurnStartedEvent,
)
from harnessed.events.turn import TurnLimits
from harnessed.models import GenerationSettings, OutputSchema, UserMessage
from harnessed.output.contracts import OutputValues
from harnessed.sessions import SessionState
from harnessed.sessions.log import SessionLog
from harnessed.sessions.result import RunResult, RunResults

from .attachments import Attachment
from .control import SessionControl
from .handles import RunEvents, RunHandle, RunStream, SubmittedRun
from .inbox import InboxWriter


class RunSubmissions:
    """Own submitted runs from inbox admission until their handle completes.

    A run is keyed by its input id while queued and by its turn id once a turn
    claims it. Only this component completes a handle, exactly once.
    """

    def __init__(
        self,
        log: SessionLog,
        control: SessionControl,
        writer: InboxWriter,
        *,
        results: RunResults,
        values: OutputValues,
    ) -> None:
        self._log = log
        self._control = control
        self._writer = writer
        self._results = results
        self._values = values
        self._runs: dict[RunHandle[Any], SubmittedRun] = {}
        self._queued: dict[str, SubmittedRun] = {}
        self._turns: dict[str, SubmittedRun] = {}

    @overload
    async def enqueue(
        self,
        content: str | UserMessage,
        *,
        attachments: Iterable[Attachment] = (),
        settings: GenerationSettings | None = None,
        limits: TurnLimits,
        schema: OutputSchema | None,
        on_prepared: Callable[[InboxAddedEvent], None] | None = None,
        events: RunEvents[Any],
    ) -> RunStream[Any]: ...

    @overload
    async def enqueue(
        self,
        content: str | UserMessage,
        *,
        attachments: Iterable[Attachment] = (),
        settings: GenerationSettings | None = None,
        limits: TurnLimits,
        schema: OutputSchema | None,
        on_prepared: Callable[[InboxAddedEvent], None] | None = None,
        events: None = None,
    ) -> RunHandle[Any]: ...

    async def enqueue(
        self,
        content: str | UserMessage,
        *,
        attachments: Iterable[Attachment] = (),
        settings: GenerationSettings | None = None,
        limits: TurnLimits,
        schema: OutputSchema | None,
        on_prepared: Callable[[InboxAddedEvent], None] | None = None,
        events: RunEvents[Any] | None = None,
    ) -> RunHandle[Any]:
        self._values.hold(schema)
        submitted = None

        def prepared(item: InboxAddedEvent) -> None:
            nonlocal submitted
            submitted = SubmittedRun(item, schema, self._cancel, events)
            self._runs[submitted.handle] = submitted
            self._queued[item.message.id] = submitted
            if on_prepared is not None:
                on_prepared(item)

        try:
            await self._writer.send(
                content,
                InboxMode.FOLLOWUP,
                attachments=attachments,
                settings=settings,
                output_schema=schema,
                limits=limits,
                prepared=prepared,
            )
        except BaseException:
            if submitted is None:
                self._values.release(schema)
            raise
        if submitted is None:
            raise SessionStateError("The inbox accepted input without preparing its run")
        return submitted.handle

    def observe(self, event: DurableEvent, *, boundary: SessionState) -> None:
        """Advance the run this committed record claims, discards or finishes.

        boundary is the conversation state at the latest closed turn; it backs
        results while a turn is open or has just ended.
        """
        submitted = None
        if isinstance(event, TurnStartedEvent):
            submitted = self._queued.pop(event.input_id, None)
            if submitted is not None:
                submitted.turn_id = event.turn_id
                self._turns[event.turn_id] = submitted
            return
        if isinstance(event, InboxRemovedEvent):
            submitted = self._queued.pop(event.message_id, None)
            records = (submitted.input, event) if submitted is not None else ()
        elif isinstance(event, TurnEndedEvent):
            submitted = self._turns.get(event.turn_id)
            records = (
                (submitted.input, *self._log.turns[-1].events) if submitted is not None else ()
            )
        else:
            return
        if submitted is not None:
            try:
                value = (
                    SessionClosed("This session closed before the turn finished")
                    if self._control.closing
                    else self._control.error
                ) or self._results.build(
                    records,
                    schema=submitted.schema,
                    state=(
                        boundary
                        if isinstance(event, TurnEndedEvent) or self._log.open_turn is not None
                        else self._log.state()
                    ),
                )
            except Exception as error:
                # Settle the waiter on any driver or persistence failure.
                value = error
            self._finish(submitted, value)

    def fail(self, failure: Exception) -> None:
        for submitted in tuple(self._runs.values()):
            self._finish(submitted, failure)

    async def _cancel(self, handle: RunHandle[Any]) -> None:
        submitted = self._runs.get(handle)
        if submitted is None:
            return
        if submitted.turn_id is None:
            try:
                await self._log.append(
                    InboxRemovedEvent(handle.id, InboxRemovalReason.DISCARDED),
                    expected_input=submitted.input,
                )
                return
            except RunUnavailable:
                # The driver may have claimed the input while removal waited.
                pass
        if submitted.turn_id is not None and not handle.done():
            await self._control.cancel_turn(submitted.turn_id)

    def _finish(self, submitted: SubmittedRun, value: RunResult[Any] | Exception) -> None:
        self._runs.pop(submitted.handle, None)
        if self._queued.get(submitted.handle.id) is submitted:
            self._queued.pop(submitted.handle.id)
        if submitted.turn_id is not None:
            self._turns.pop(submitted.turn_id, None)
        submitted.finish(value)
        self._values.release(submitted.schema)
