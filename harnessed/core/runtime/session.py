from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from typing import Any, Unpack, overload

from harnessed.core.assets import AssetStore
from harnessed.core.attachments import Attachment
from harnessed.core.buffering import EventBuffer
from harnessed.core.compaction import CompactionManager
from harnessed.core.errors import RunUnavailable, SessionClosed, SessionStateError
from harnessed.core.events.base import DurableEvent, TransientEvent
from harnessed.core.events.compaction import CompactionAppliedEvent
from harnessed.core.events.inbox import (
    InboxAddedEvent,
    InboxMode,
    InboxRemovalReason,
    InboxRemovedEvent,
)
from harnessed.core.events.publishing import SessionPublished
from harnessed.core.events.session import SessionControlChangedEvent
from harnessed.core.events.stream import StreamItem, coalesce_deltas
from harnessed.core.events.subagents import SubagentLinkedEvent
from harnessed.core.events.turn import TurnEndedEvent, TurnLimits, TurnStartedEvent
from harnessed.core.hooks import (
    SessionAppended,
    SessionClosing,
)
from harnessed.core.messages import Message, UserMessage
from harnessed.core.model import (
    GenerationSettings,
    Model,
    ModelRequest,
    ModelUsage,
    OutputSchema,
    resolve_request_settings,
)
from harnessed.core.outputs import OutputContract, OutputTypes, OutputValues
from harnessed.core.plugin import plugin_contribution, plugin_events
from harnessed.core.prompt.delivery import runtime_sections
from harnessed.core.prompt.system_prompt import SystemPromptStore
from harnessed.core.prompt.values import SectionDelivery
from harnessed.core.result import RunResult, RunResults
from harnessed.core.runtime.approvals import LiveApprovals, SessionApprovals
from harnessed.core.runtime.context import PendingObservations, SessionContext
from harnessed.core.runtime.control import SessionControl, SessionStatus, StopAtBoundary
from harnessed.core.runtime.executor import ToolExecutor
from harnessed.core.runtime.handles import RunEvents, RunHandle, RunStream, SubmittedRun
from harnessed.core.runtime.inbox import InboxItem, InboxWriter, SessionInbox
from harnessed.core.runtime.options import AgentOptions, RunOptions
from harnessed.core.runtime.output import OutputHandler
from harnessed.core.runtime.respond import ModelResponder
from harnessed.core.runtime.stepper import StepRunner
from harnessed.core.runtime.stream_approvals import StreamApprovals
from harnessed.core.runtime.streaming import project_stream_events
from harnessed.core.runtime.turns import TurnRunner
from harnessed.core.session.children import ChildSession
from harnessed.core.session.session import SessionLog
from harnessed.core.session.state import SessionState
from harnessed.core.session.turns import Turn
from harnessed.core.tools import ToolRegistry
from harnessed.core.tools.context import BoundToolContexts, ToolContexts
from harnessed.core.tools.view import SessionTools
from harnessed.core.tracing import TraceSession, bind_tracer
from harnessed.kernel import (
    Disposer,
    HookFactory,
    ListenerFactory,
    PluginHandle,
    PluginLike,
    Scope,
)

_driving: ContextVar[Session | None] = ContextVar("harnessed_driving", default=None)


class Session:
    """A live conversation with in-memory inputs, turn-scoped results and controls."""

    def __init__(self, scope: Scope, session: SessionLog, options: AgentOptions) -> None:
        self.scope = scope
        self._tools_view = SessionTools(scope[ToolRegistry], scope)
        self.log = session
        self._options = options
        self._close_task: asyncio.Task[None] | None = None
        self.id = session.id
        scope.provide(TraceSession, TraceSession(self.id))
        self._boundary_state = session.boundary_state
        self._compaction = CompactionManager(scope, session, context_window=options.context_window)
        self.control = SessionControl(session, self._drive, lambda: _driving.get() is self)
        self._live_approvals = LiveApprovals()
        self.approvals = SessionApprovals(scope, session, self.control, self._live_approvals)
        self._inbox_writer = InboxWriter(session, self.control)
        self.inbox = SessionInbox(session, self.control, self._inbox_writer)
        observations = PendingObservations()
        self._context = SessionContext(
            session, self.control, self.inbox, self.approvals, self._tools_view, observations
        )
        scope.provide(SessionControl, self.control)
        scope.provide(SessionInbox, self.inbox)
        scope.provide(ToolContexts, BoundToolContexts(session, self.inbox, self.approvals))
        self._subscribers: set[EventBuffer[DurableEvent | TransientEvent | None]] = set()
        self._submissions: dict[RunHandle[Any], SubmittedRun] = {}
        self._queued_submissions: dict[str, SubmittedRun] = {}
        self._submitted_turns: dict[str, SubmittedRun] = {}
        self._output_values = OutputValues()
        if OutputTypes not in scope:
            scope.provide(OutputTypes, OutputTypes())
        self._results = RunResults(scope[OutputTypes], self._output_values)
        self._steps = StepRunner(
            scope,
            session,
            options,
            self.control,
            self._compaction,
            OutputHandler(scope, session, options, self._output_values),
            ToolExecutor(scope, session, options, self.approvals, self._context),
            ModelResponder(scope, session),
            self._context,
            observations,
        )
        self._runner = TurnRunner(scope, session, options, self.control, self._steps, self._context)
        session.set_persistence_error_handler(self._persistence_failed)
        scope.on(SessionAppended, self._session_event)
        scope.on(SessionPublished, self._stream_event)

    @property
    def plugin_context(self) -> SessionContext:
        """The restricted view that plugins and hooks receive instead of this Session.

        It reads state, contributes context, emits events and requests approvals,
        but it cannot start turns, mount plugins or close the session.
        """
        return self._context

    @property
    def tools(self) -> SessionTools:
        """Inspect currently visible tools without changing their registrations."""
        return self._tools_view

    @property
    def prompt(self) -> SystemPromptStore:
        return self.scope[SystemPromptStore]

    @property
    def events(self) -> tuple[DurableEvent, ...]:
        return self.log.events

    @property
    def messages(self) -> tuple[Message, ...]:
        return self.log.messages

    @property
    def usage(self) -> ModelUsage:
        return self.log.usage

    @property
    def tree_usage(self) -> ModelUsage:
        """Local usage plus acknowledged child receipts; active children may lag."""
        return self.log.tree_usage

    @property
    def children(self) -> tuple[ChildSession, ...]:
        return self.log.children

    @property
    def parent(self) -> SubagentLinkedEvent | None:
        return self.log.parent

    @property
    def turns(self) -> tuple[Turn, ...]:
        return self.log.turns

    def last[E: DurableEvent](self, kind: type[E]) -> E | None:
        return self.log.last(kind)

    def snapshot(self) -> SessionState:
        """Export an idle conversation without retaining its runtime or doing I/O."""
        self.control.check_open()
        if (
            self.log.open_turn is not None
            or self.log.has_wakeup
            or self.status is SessionStatus.PAUSED
        ):
            raise SessionStateError("Export requires an idle session with no queued input")
        return self.log.snapshot()

    async def emit(self, event: DurableEvent | TransientEvent) -> None:
        await self._context.emit(event)

    async def flush(self) -> None:
        """Wait for the latest checkpoint; no I/O occurs without a configured store."""
        self.control.check_open()
        await self.log.flush()

    async def subscribe(
        self, *, transient: bool = False, buffer_size: int = 256
    ) -> AsyncIterator[DurableEvent | TransientEvent]:
        """Observe the raw log independently of any individual run/stream call."""
        async for event in self.log.subscribe(transient=transient, buffer_size=buffer_size):
            yield event

    @asynccontextmanager
    async def use(
        self, plugin: PluginLike, *, section_delivery: SectionDelivery = SectionDelivery.ERROR
    ) -> AsyncGenerator[PluginHandle]:
        """Mount a capability for this context and remove it on every exit path."""
        handle = await self.mount(plugin, section_delivery=section_delivery)
        try:
            yield handle
        finally:
            await handle.aclose()

    async def mount(
        self, plugin: PluginLike, *, section_delivery: SectionDelivery = SectionDelivery.ERROR
    ) -> PluginHandle:
        """Mount a capability, with explicit delivery of any declared system sections.

        CONTEXT owns guidance for each activation; IGNORE never evaluates section
        providers. Neither policy changes the session's fixed system text.
        """
        delivery = SectionDelivery(section_delivery)
        contribution = plugin_contribution(plugin)
        spec = runtime_sections(plugin, contribution, delivery)
        self.log.registry.register(plugin_events(plugin))
        handle = await self.scope.plugin(spec)
        try:
            await self.scope.settle()
            self.scope[ToolRegistry].validate_references()
        except BaseException:
            await handle.aclose()
            raise
        return handle

    @overload
    def on[H](
        self,
        event: HookFactory[H],
        fn: None = None,
        *,
        prepend: bool = False,
        priority: int = 0,
        owner: Scope | None = None,
    ) -> H: ...

    @overload
    def on[C](
        self,
        event: ListenerFactory[C],
        fn: C,
        *,
        prepend: bool = False,
        priority: int = 0,
        owner: Scope | None = None,
    ) -> Disposer: ...

    def on(self, event, fn=None, *, prepend: bool = False, priority: int = 0, owner=None):
        owner = owner if owner is not None else Scope.current() or self.scope
        return owner.on(event, fn, prepend=prepend, priority=priority)

    def add_hook(
        self, event, callback, *, prepend: bool = False, priority: int = 0, owner=None
    ) -> Disposer:
        owner = owner if owner is not None else Scope.current() or self.scope
        return owner.add_listener(event, callback, prepend=prepend, priority=priority)

    @property
    def assets(self) -> AssetStore:
        """Read and write assets owned by this session's configured backend."""
        return self.log.assets

    @property
    def status(self) -> SessionStatus:
        return self.control.status

    def start(self) -> None:
        self.control.start()

    async def steer(
        self, content: str | UserMessage, *, attachments: Iterable[Attachment] = ()
    ) -> InboxItem:
        """Add input to the running turn's next step, or start a turn while idle."""
        return await self.inbox.steer(content, attachments=attachments)

    async def append_message(self, message: UserMessage) -> None:
        """Append a user message to history without starting a turn.

        Each call appends to conversation history, including repeated messages.
        It follows ordinary history retention and may be summarized by compaction.
        Await this method before starting work that must see the contribution.
        A configured session store commits the contribution before this returns.
        A message ID still queued in the inbox raises ValueError without changing
        history or the queued run; use a fresh UserMessage for additive input.
        """
        await self._context.append_message(message)

    def observe(self, message: UserMessage, *, source: str, refs: Iterable[str] = ()) -> None:
        """Buffer a text/image snapshot per source for the next step.

        A newer pending snapshot replaces the same source before recording.
        Recorded snapshots remain unchanged until compaction or an explicit
        observation retention policy removes them from model history.
        """
        self._context.observe(message, source=source, refs=refs)

    async def pause(self) -> None:
        """Wait until the current model call and all its tools are committed."""
        await self.control.pause()

    async def resume(self) -> None:
        """Resume a paused conversation; safe to call repeatedly."""
        await self.control.resume()

    async def wait_until_idle(self) -> None:
        """Wait for the session to become idle with an empty inbox."""
        await self.control.wait_until_idle()

    async def compact(self) -> CompactionAppliedEvent | None:
        """Summarize completed history while idle or paused; return its log record."""
        with bind_tracer(self.scope):
            return await self._compact()

    async def _compact(self) -> CompactionAppliedEvent | None:
        async with self.control.maintenance():
            model = self.scope[Model]
            request = ModelRequest(
                self.log.messages,
                self.scope[ToolRegistry].visible_definitions(self.scope),
            )
            request = resolve_request_settings(request, model.settings)
            applied, _ = await self._compaction.compact(request, model=model)
            if self.log.open_turn is None:
                self._boundary_state = self.log.snapshot()
            return applied

    def _wake(self) -> None:
        self.control.wake()

    def _session_event(self, event: SessionAppended) -> None:
        if event.log is self.log:
            if isinstance(event.event, TurnEndedEvent):
                self._runner.record_turn(self.log.turns[-1])
                self._boundary_state = self.log.snapshot()
            self._submission_event(event.event)
            if isinstance(event.event, SessionControlChangedEvent):
                self.control.synchronize()
            else:
                self.control.notify()
            self._publish(event.event)

    def _submission_event(self, event: DurableEvent) -> None:
        submitted = None
        if isinstance(event, TurnStartedEvent):
            submitted = self._queued_submissions.pop(event.input_id, None)
            if submitted is not None:
                submitted.turn_id = event.turn_id
                self._submitted_turns[event.turn_id] = submitted
            return
        if isinstance(event, InboxRemovedEvent):
            submitted = self._queued_submissions.pop(event.message_id, None)
            records = (submitted.input, event) if submitted is not None else ()
        elif isinstance(event, TurnEndedEvent):
            submitted = self._submitted_turns.get(event.turn_id)
            records = (submitted.input, *self.log.turns[-1].events) if submitted is not None else ()
        else:
            return
        if submitted is not None:
            try:
                value = (
                    SessionClosed("This session closed before the turn finished")
                    if self.control.closing
                    else self.control.error
                ) or self._results.build(
                    records,
                    schema=submitted.schema,
                    state=(
                        self._boundary_state
                        if isinstance(event, TurnEndedEvent) or self.log.open_turn is not None
                        else self.log.snapshot()
                    ),
                )
            except Exception as error:
                value = error
            self._finish_submission(submitted, value)

    def _finish_submission(
        self, submitted: SubmittedRun, value: RunResult[Any] | Exception
    ) -> None:
        self._submissions.pop(submitted.handle, None)
        if self._queued_submissions.get(submitted.handle.id) is submitted:
            self._queued_submissions.pop(submitted.handle.id)
        if submitted.turn_id is not None:
            self._submitted_turns.pop(submitted.turn_id, None)
        submitted.finish(value)
        self._output_values.release(submitted.schema)

    def _publish(self, event: DurableEvent | TransientEvent | None) -> None:
        if event is None:
            failure = self.control.error
            if failure is None and (self.control.closing or self.status is SessionStatus.CLOSED):
                failure = SessionClosed(
                    "This handle closed before the turn finished; "
                    "use an earlier conversation snapshot to continue"
                )
            if failure is not None:
                for submitted in tuple(self._submissions.values()):
                    self._finish_submission(submitted, failure)
        for queue in tuple(self._subscribers):
            if not queue.put(event):
                self._subscribers.discard(queue)

    def _stream_event(self, event: SessionPublished) -> None:
        if event.log is self.log:
            self._publish(event.event)

    def _persistence_failed(self, error: Exception) -> None:
        self.control.fail(error)
        self._publish(None)

    @overload
    async def run[T](
        self,
        content: str | UserMessage,
        *,
        output: type[T] | OutputContract[T],
        **options: Unpack[RunOptions],
    ) -> RunResult[T]: ...

    @overload
    async def run(
        self, content: str | UserMessage, *, output: None = None, **options: Unpack[RunOptions]
    ) -> RunResult[None]: ...

    async def run(
        self,
        content: str | UserMessage,
        *,
        attachments: Iterable[Attachment] = (),
        settings: GenerationSettings | None = None,
        max_steps: int | None = None,
        token_budget: int | None = None,
        output: type[Any] | OutputContract[Any] | None = None,
    ) -> RunResult[Any]:
        handle = await self.submit(
            content,
            attachments=attachments,
            settings=settings,
            max_steps=max_steps,
            token_budget=token_budget,
            output=output,
        )
        return await handle.result()

    @overload
    async def submit[T](
        self,
        content: str | UserMessage,
        *,
        output: type[T] | OutputContract[T],
        **options: Unpack[RunOptions],
    ) -> RunHandle[T]: ...

    @overload
    async def submit(
        self, content: str | UserMessage, *, output: None = None, **options: Unpack[RunOptions]
    ) -> RunHandle[None]: ...

    async def submit(
        self,
        content: str | UserMessage,
        *,
        attachments: Iterable[Attachment] = (),
        settings: GenerationSettings | None = None,
        max_steps: int | None = None,
        token_budget: int | None = None,
        output: type[Any] | OutputContract[Any] | None = None,
    ) -> RunHandle[Any]:
        """Admit a turn to the in-memory inbox and return its session-owned completion handle."""
        self.control.check_open()
        limits = TurnLimits(max_steps, token_budget)
        compiled = self.scope[OutputTypes].register(output) if output is not None else None
        schema = compiled.definition if compiled is not None else None
        return await self._enqueue(
            content,
            attachments=attachments,
            settings=settings,
            limits=limits,
            schema=schema,
        )

    @overload
    async def _enqueue(
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
    async def _enqueue(
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

    async def _enqueue(
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
        self._output_values.hold(schema)
        submitted = None

        def prepared(item: InboxAddedEvent) -> None:
            nonlocal submitted
            submitted = SubmittedRun(item, schema, self._cancel_submission, events)
            self._submissions[submitted.handle] = submitted
            self._queued_submissions[item.message.id] = submitted
            if on_prepared is not None:
                on_prepared(item)

        try:
            await self._inbox_writer.send(
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
                self._output_values.release(schema)
            raise
        if submitted is None:
            raise SessionStateError("The inbox accepted input without preparing its run")
        return submitted.handle

    async def _cancel_submission(self, handle: RunHandle[Any]) -> None:
        submitted = self._submissions.get(handle)
        if submitted is None:
            return
        if submitted.turn_id is None:
            try:
                await self.log.append(
                    InboxRemovedEvent(handle.id, InboxRemovalReason.DISCARDED),
                    expected_input=submitted.input,
                )
                return
            except RunUnavailable:
                # The driver may have claimed the input while removal waited.
                pass
        if submitted.turn_id is not None and not handle.done():
            await self.control.cancel_turn(submitted.turn_id)

    @overload
    def stream[T](
        self,
        content: str | UserMessage,
        *,
        output: type[T] | OutputContract[T],
        buffer_size: int = 256,
        **options: Unpack[RunOptions],
    ) -> AbstractAsyncContextManager[RunStream[T]]: ...

    @overload
    def stream(
        self,
        content: str | UserMessage,
        *,
        output: None = None,
        buffer_size: int = 256,
        **options: Unpack[RunOptions],
    ) -> AbstractAsyncContextManager[RunStream[None]]: ...

    @asynccontextmanager
    async def stream(
        self,
        content: str | UserMessage,
        *,
        attachments: Iterable[Attachment] = (),
        settings: GenerationSettings | None = None,
        max_steps: int | None = None,
        token_budget: int | None = None,
        output: type[Any] | OutputContract[Any] | None = None,
        buffer_size: int = 256,
    ) -> AsyncGenerator[RunStream[Any]]:
        """Queue one turn and own its live event observation for this context.

        Iterate the RunStream for UI and declared application events, or await its
        result independently. Context exit removes its approval responder and
        observation without aborting work; handle.cancel() targets only this run.
        """
        self.control.check_open()
        queue: EventBuffer[DurableEvent | TransientEvent | None] = EventBuffer(
            buffer_size, merge=coalesce_deltas
        )
        limits = TurnLimits(max_steps, token_budget)
        compiled = self.scope[OutputTypes].register(output) if output is not None else None
        schema = compiled.definition if compiled is not None else None

        def events(
            input_id: str, completion: Callable[[], Awaitable[RunResult[Any]]]
        ) -> AsyncGenerator[StreamItem[Any], None]:
            return project_stream_events(self._stream_turn(queue, input_id), completion=completion)

        async with StreamApprovals(self.scope, self.log, self._live_approvals) as approvals:
            self._subscribers.add(queue)
            try:
                handle = await self._enqueue(
                    content,
                    attachments=attachments,
                    settings=settings,
                    limits=limits,
                    schema=schema,
                    on_prepared=approvals.prepared,
                    events=events,
                )
                try:
                    yield handle
                finally:
                    await handle.aclose()
            finally:
                self._subscribers.discard(queue)

    async def stop(self, *, keep_inbox: bool = False) -> None:
        """Finish the current step, end its turn and return to idle."""
        await self.control.stop(keep_inbox=keep_inbox)

    async def abort(self, *, keep_inbox: bool = False) -> None:
        """Cancel current activity, repair its log and return to idle."""
        await self.control.abort(keep_inbox=keep_inbox)

    async def aclose(self) -> None:
        """Close once; concurrent callers share cleanup, independently of cancellation."""
        if _driving.get() is self or asyncio.current_task() is self._close_task:
            raise SessionStateError("A driver or closing hook cannot wait for its own closure")
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name=f"harnessed:close:{self.id}")
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        try:
            await self.control.close(preserve_paused=self.log.persistent)
        finally:
            try:
                if self.control.closing:
                    with bind_tracer(self.scope):
                        await self.scope.dispatch(SessionClosing(self._context))
            finally:
                try:
                    await self.log.close_persistence()
                finally:
                    self._publish(None)

    async def _drive(self) -> None:
        token = _driving.set(self)
        try:
            while self.log.has_wakeup or self.log.open_turn is not None:
                await self.control.checkpoint()
                if not await self._runner.run():
                    break
        except (StopAtBoundary, asyncio.CancelledError):
            try:
                if not self.control.suspending:
                    await self.control.finish_termination()
            except Exception as exc:
                self.control.error = exc
        except Exception as exc:
            self.control.error = exc
            try:
                await self.control.finish_termination()
            except Exception as repair_error:
                if repair_error is not exc:
                    self.control.error = ExceptionGroup(
                        "Driver and session repair failed", [exc, repair_error]
                    )
        finally:
            try:
                await self.control.finish_driver()
            except Exception as exc:
                self.control.error = exc
            _driving.reset(token)
            self._publish(None)

    async def _stream_turn(
        self,
        queue: EventBuffer[DurableEvent | TransientEvent | None],
        input_id: str,
    ) -> AsyncGenerator[DurableEvent | TransientEvent, None]:
        try:
            turn_id = None
            while True:
                event = await queue.get()
                if event is None:
                    if self.control.error is not None:
                        raise self.control.error
                    if self.control.closing or self.status is SessionStatus.CLOSED:
                        raise SessionClosed(
                            "This handle closed before the turn finished; "
                            "use an earlier conversation snapshot to continue"
                        )
                    if any(item.id == input_id for item in self.inbox.pending):
                        self._wake()
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
                    if self.control.error is not None:
                        raise self.control.error
                    return
        finally:
            self._subscribers.discard(queue)
