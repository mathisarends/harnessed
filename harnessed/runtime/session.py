from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from typing import Any, Unpack, overload

from harnessed.approvals.session import LiveApprovals, SessionApprovals
from harnessed.approvals.stream import StreamApprovals
from harnessed.context import ContextMeter
from harnessed.context.compaction import CompactionManager
from harnessed.errors import SessionClosed, SessionStateError
from harnessed.events import (
    CompactionAppliedEvent,
    DurableEvent,
    SessionControlChangedEvent,
    SubagentLinkedEvent,
    TransientEvent,
    TurnEndedEvent,
)
from harnessed.events.stream import StreamItem
from harnessed.events.turn import TurnLimits
from harnessed.kernel import (
    Scope,
)
from harnessed.models import (
    GenerationSettings,
    Message,
    Model,
    ModelRequest,
    ModelUsage,
    UserMessage,
)
from harnessed.models.assets import AssetStore
from harnessed.models.model import resolve_request_settings
from harnessed.output import OutputContract
from harnessed.output.contracts import OutputTypes, OutputValues
from harnessed.output.handler import OutputHandler
from harnessed.prompts import SectionDelivery, SystemPromptStore
from harnessed.sessions import ChildSession, SessionState
from harnessed.sessions.context import PendingObservations, SessionContext
from harnessed.sessions.log import SessionLog
from harnessed.sessions.publishing import SessionPublished
from harnessed.sessions.result import RunResult, RunResults
from harnessed.sessions.turns import Turn
from harnessed.tools import BoundToolContexts, SessionTools, ToolContexts, ToolRegistry
from harnessed.tracing import TraceSession, bind_tracer

from .attachments import Attachment
from .control import SessionControl, SessionStatus, StopAtBoundary
from .executor import ToolExecutor
from .handles import RunHandle, RunStream
from .hooks import (
    SessionAppended,
    SessionClosing,
)
from .inbox import InboxItem, InboxWriter, SessionInbox
from .mounts import PluginMount
from .options import AgentOptions, RunOptions
from .plugin import Plugin
from .respond import ModelResponder
from .stepper import StepRunner
from .streaming import TurnStreams, project_stream_events
from .submissions import RunSubmissions
from .turns import TurnRunner

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
        self._compaction = CompactionManager(scope, session)
        scope.provide(ContextMeter, ContextMeter(scope, session))
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
        self._streams = TurnStreams(self.control, self.inbox)
        output_values = OutputValues()
        if OutputTypes not in scope:
            scope.provide(OutputTypes, OutputTypes())
        self._submissions = RunSubmissions(
            session,
            self.control,
            self._inbox_writer,
            results=RunResults(scope[OutputTypes], output_values),
            values=output_values,
        )
        self._steps = StepRunner(
            scope,
            session,
            options,
            self.control,
            self._compaction,
            OutputHandler(scope, session, options, output_values),
            ToolExecutor(scope, session, options, self.approvals, self._context),
            ModelResponder(scope, session, self._context),
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
    def model_request_count(self) -> int:
        """Count local ModelRequestedEvent records, including failed invocations."""
        return self.log.model_request_count

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

    def mount(
        self, plugin: Plugin, *, section_delivery: SectionDelivery = SectionDelivery.CONTEXT
    ) -> PluginMount:
        """Mount a capability using async with, or await a manually owned handle."""
        return PluginMount(self.scope, self.log.registry, plugin, section_delivery=section_delivery)

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

    def _session_event(self, event: SessionAppended) -> None:
        if event.log is self.log:
            if isinstance(event.event, TurnEndedEvent):
                self._runner.record_turn(self.log.turns[-1])
                self._boundary_state = self.log.snapshot()
            self._submissions.observe(event.event, boundary=self._boundary_state)
            if isinstance(event.event, SessionControlChangedEvent):
                self.control.synchronize()
            else:
                self.control.notify()
            self._publish(event.event)

    def _publish(self, event: DurableEvent | TransientEvent | None) -> None:
        if event is None:
            failure = self.control.error
            if failure is None and (self.control.closing or self.status is SessionStatus.CLOSED):
                failure = SessionClosed(
                    "This handle closed before the turn finished; "
                    "use an earlier conversation snapshot to continue"
                )
            if failure is not None:
                self._submissions.fail(failure)
        self._streams.publish(event)

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
        return await self._submissions.enqueue(
            content,
            attachments=attachments,
            settings=settings,
            limits=limits,
            schema=schema,
        )

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
        limits = TurnLimits(max_steps, token_budget)
        compiled = self.scope[OutputTypes].register(output) if output is not None else None
        schema = compiled.definition if compiled is not None else None
        async with StreamApprovals(self.scope, self.log, self._live_approvals) as approvals:
            queue = self._streams.attach(buffer_size)

            def events(
                input_id: str, completion: Callable[[], Awaitable[RunResult[Any]]]
            ) -> AsyncGenerator[StreamItem[Any], None]:
                return project_stream_events(
                    self._streams.follow(queue, input_id), completion=completion
                )

            try:
                handle = await self._submissions.enqueue(
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
                self._streams.detach(queue)

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
                # Record any repair failure so cancelled drivers still publish their terminal state.
                self.control.error = exc
        except Exception as exc:
            # Retain arbitrary model and plugin failures for session consumers.
            self.control.error = exc
            try:
                await self.control.finish_termination()
            except Exception as repair_error:
                # Keep both failures when termination repair also fails after a driver error.
                if repair_error is not exc:
                    self.control.error = ExceptionGroup(
                        "Driver and session repair failed", [exc, repair_error]
                    )
        finally:
            try:
                await self.control.finish_driver()
            except Exception as exc:
                # Surface any cleanup failure before publishing driver completion.
                self.control.error = exc
            _driving.reset(token)
            self._publish(None)
