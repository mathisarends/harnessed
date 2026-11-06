import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable
from functools import partial
from typing import cast

from harnessed.core.approvals import Approval, ApprovalState, approval_transition
from harnessed.core.assets import AssetStore, _MemoryAssets
from harnessed.core.buffering import EventBuffer
from harnessed.core.errors import SessionFormatError, SessionStateError
from harnessed.core.events.approvals import ApprovalResolvedEvent, ApprovalStatus
from harnessed.core.events.base import DurableEvent, TransientEvent
from harnessed.core.events.compaction import CompactionAppliedEvent
from harnessed.core.events.inbox import (
    InboxAddedEvent,
    InboxMode,
    InboxRemovalReason,
    InboxRemovedEvent,
    InboxTarget,
)
from harnessed.core.events.messages import (
    AssistantMessageEvent,
    ContextMessageEvent,
    UserMessageEvent,
)
from harnessed.core.events.model import (
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelRequestOutcome,
    ModelUsageRecordedEvent,
)
from harnessed.core.events.observations import IncludedObservation
from harnessed.core.events.publishing import SessionPublished
from harnessed.core.events.registry import EventRegistry
from harnessed.core.events.session import ControlState, SessionControlChangedEvent
from harnessed.core.events.stream import coalesce_deltas
from harnessed.core.events.subagents import (
    SubagentFinishedEvent,
    SubagentLinkedEvent,
    SubagentStatus,
)
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
from harnessed.core.events.unions import SESSION_EVENT_TYPES, LogEvent
from harnessed.core.hooks import SessionAppended
from harnessed.core.messages import (
    AssistantMessage,
    ImageReference,
    Message,
    SpillRef,
    ToolCall,
    ToolMessage,
    ToolResult,
    ToolStatus,
)
from harnessed.core.model import ModelRequest, ModelUsage, sum_usage
from harnessed.core.session.checkpoint import (
    CheckpointEntry,
    CheckpointExtension,
    CheckpointSeed,
    SessionCheckpoint,
)
from harnessed.core.session.children import ChildProjection, ChildSession
from harnessed.core.session.commit import SessionChange
from harnessed.core.session.control import RunControl
from harnessed.core.session.files import MemoryFiles
from harnessed.core.session.history import (
    WorkingHistory,
)
from harnessed.core.session.history import derive_messages as derive_messages
from harnessed.core.session.history import derive_requests as derive_requests
from harnessed.core.session.images import _SessionImages
from harnessed.core.session.inbox import Inbox
from harnessed.core.session.state import HistoryTurn, SessionState, image_refs
from harnessed.core.session.store import SessionPersistence
from harnessed.core.session.turns import Turn, TurnProjection
from harnessed.kernel import Scope


class SessionLog:
    """Own live conversation state and the current turn's execution events."""

    def __init__(
        self,
        session_id: str,
        *,
        scope: Scope | None = None,
        state: SessionState | None = None,
        files: MemoryFiles | None = None,
        registry: EventRegistry | None = None,
        assets: _MemoryAssets | None = None,
        persistence: SessionPersistence | None = None,
    ) -> None:
        if registry is None:
            registry = (
                scope[EventRegistry]
                if scope is not None and EventRegistry in scope
                else EventRegistry()
            )
        self.registry = registry
        self.id = session_id
        self.scope = scope
        self._events: list[DurableEvent] = []
        self._snapshot: tuple[DurableEvent, ...] | None = None
        self._last: dict[type[DurableEvent], tuple[int, DurableEvent]] = {}
        self._files = files if files is not None else MemoryFiles(state.files if state else ())
        self._assets = (
            assets if assets is not None else _MemoryAssets(state.assets if state else ())
        )
        self._images = _SessionImages(self._assets)
        self._usage: ModelUsage | None = None
        self._observers: dict[EventBuffer[DurableEvent | TransientEvent | None], bool] = {}
        self._subscriptions_closed = False
        self._lock = asyncio.Lock()
        self._repair_lock = asyncio.Lock()
        self._inbox: Inbox | None = Inbox(())
        self._pending: dict[str, InboxAddedEvent] = {}
        self._step_pending: set[str] = set()
        self._approvals: ApprovalState | None = ApprovalState(())
        self._approval_records: dict[str, Approval] = {}
        self._approvals_by_call: dict[str, dict[str, Approval]] = {}
        self._grants: dict[tuple[str, str], dict[str, Approval]] = {}
        self._control = RunControl(())
        self._requests: list[ModelRequest] = []
        self._requests_by_id: dict[str, ModelRequest] = {}
        self._finished_requests: set[str] = set()
        self._open_executions: set[str] = set()
        self._request_snapshot: tuple[ModelRequest, ...] | None = None
        self._turns = TurnProjection()
        self._children = ChildProjection(
            state.children if state else (), state.parent if state else None
        )
        self._turn_boundaries = [(turn.boundary, turn.id) for turn in state.turns] if state else []
        self._known_turns = {turn.id for turn in state.turns} if state else set()
        self._history = WorkingHistory(state)
        self._sequence = max(
            [
                0,
                state.compacted_through,
                *(entry.position + 1 for entry in state.history),
                *(turn.boundary for turn in state.turns),
            ]
            if state
            else [0]
        )
        self._compacted_through = state.compacted_through if state else 0
        self._preserved_turns = state.preserved_turns if state else ()
        self._uncompacted_turns = state.turns_since_compaction if state else 0
        self._closed_count = len(self._turn_boundaries)
        self._persistence = persistence
        self._restoring = False
        self._turn_state: SessionState | None = None
        self._turn_seed: tuple[CheckpointSeed, ...] = ()
        self._turn_offset = self._sequence
        if persistence is not None:
            persistence.bind(partial(self._flush_checkpoint, report=False))

    @property
    def persistent(self) -> bool:
        return self._persistence is not None

    @property
    def persistence_failed(self) -> bool:
        return self._persistence is not None and self._persistence.failed

    async def flush(self) -> None:
        """Commit preceding changes, draining a write even if the waiter is cancelled."""
        if self._persistence is None:
            return
        task = asyncio.create_task(self._flush_checkpoint())
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    def set_persistence_error_handler(self, callback: Callable[[Exception], None]) -> None:
        if self._persistence is not None:
            self._persistence.set_error_handler(callback)

    async def close_persistence(self) -> None:
        if self._persistence is not None:
            await self._persistence.aclose()

    @property
    def boundary_state(self) -> SessionState:
        return self._turn_state if self._turn_state is not None else self.snapshot()

    async def restore(self, checkpoint: SessionCheckpoint) -> None:
        """Project a validated checkpoint without dispatching historical hooks."""
        if self._events or self.open_turn is not None or checkpoint.offset < self._sequence:
            raise SessionFormatError("Checkpoint offset precedes its conversation state")
        self._restoring = True
        try:
            for event in checkpoint.seed:
                await self._append(event)
            self._events.clear()
            self._snapshot = None
            self._sequence = checkpoint.offset
            for event in checkpoint.events:
                restored = (
                    event.to_event(registry=self.registry)
                    if isinstance(event, CheckpointExtension)
                    else event
                )
                await self._append(restored)
            if checkpoint.events and self.open_turn is None:
                raise SessionFormatError("An execution suffix must contain one open turn")
            self._control = RunControl((checkpoint.control,))
            files = {value.key: value.text for value in self._files.snapshot()}
            for ref in self.resources:
                if hashlib.sha256(files[ref.path].encode("utf-8")).hexdigest() != ref.sha256:
                    raise SessionFormatError("Checkpoint text resource integrity check failed")
            self._retain_resources()
        except (ValueError, KeyError) as error:
            raise SessionFormatError("Invalid checkpoint execution") from error
        finally:
            self._restoring = False

    def _checkpoint(self) -> SessionCheckpoint:
        turn = self.open_turn
        state = self.snapshot() if turn is None else self.boundary_state
        assets = {value.ref.sha256 for value in state.assets}
        files = {value.key: value for value in state.files}
        return SessionCheckpoint(
            state=state,
            offset=self._turn_offset if turn is not None else self._sequence,
            seed=self._turn_seed if turn is not None else self._checkpoint_seed(),
            events=tuple(
                self._checkpoint_entry(event) for event in (turn.events if turn is not None else ())
            ),
            control=SessionControlChangedEvent(self._control.state, self._control.keep_inbox),
            assets=tuple(
                value for value in self._assets.snapshot() if value.ref.sha256 not in assets
            ),
            files=tuple(value for value in self._files.snapshot() if files.get(value.key) != value),
        )

    def _checkpoint_entry(self, event: DurableEvent) -> CheckpointEntry:
        # The log admits only catalog-validated built-ins or declared extensions.
        if event.type not in SESSION_EVENT_TYPES:
            return CheckpointExtension.from_event(event, registry=self.registry)
        return cast(LogEvent, event)

    async def _flush_checkpoint(self, *, report: bool = True) -> None:
        async with self._lock:
            if self._persistence is not None:
                await self._persistence.flush(self._checkpoint, report=report)

    def _checkpoint_seed(self) -> tuple[CheckpointSeed, ...]:
        events: list[CheckpointSeed] = list(self._pending.values())
        for grant in self.approvals.grants:
            events.append(grant.request)
            if grant.resolution is not None:
                events.append(grant.resolution)
        return tuple(events)

    @property
    def next_sequence(self) -> int:
        return self._sequence

    @property
    def event_offset(self) -> int:
        return self._sequence - len(self._events)

    @property
    def compacted_through(self) -> int:
        return self._compacted_through

    @property
    def preserved_turns(self) -> tuple[str, ...]:
        return self._preserved_turns

    @property
    def files(self) -> MemoryFiles:
        return self._files

    def close_resources(self) -> None:
        self._images.clear()
        self._assets._clear()
        self._files.clear()

    @property
    def assets(self) -> AssetStore:
        """The asset backend paired with this log; ownership cannot be replaced."""
        return self._assets

    async def resolve_request(self, request: ModelRequest) -> ModelRequest:
        """Resolve stored images for manual model replay without changing the log.

        Only images in this request are read. Missing or corrupt assets fail
        explicitly. The returned transport copy can hold large image payloads;
        callers should release it after use.
        """
        return await self._images.resolve(request)

    @property
    def events(self) -> tuple[DurableEvent, ...]:
        if self._snapshot is None:
            self._snapshot = tuple(self._events)
        return self._snapshot

    @property
    def closed_turn_count(self) -> int:
        return self._closed_count

    @property
    def context_values(self) -> dict[str, str]:
        return self._history.context.copy()

    @property
    def resources(self) -> tuple[SpillRef, ...]:
        return tuple(
            dict.fromkeys(ref for entry in self._history.items() for ref in entry.resources)
        )

    def snapshot(self) -> SessionState:
        if self.open_turn is not None:
            raise SessionStateError("Export requires a terminal turn boundary")
        entries = tuple(entry for entry in self._history.items() if entry.message.id != "system")
        keys = {ref.path for entry in entries for ref in entry.resources}
        return SessionState(
            history=entries,
            context=self.context_values,
            turns=tuple(HistoryTurn(turn, boundary) for boundary, turn in self._turn_boundaries),
            turns_since_compaction=self._uncompacted_turns,
            compacted_through=self._compacted_through,
            preserved_turns=self._preserved_turns,
            observation_limit=self._history.max_observations,
            assets=self._assets.snapshot(image_refs(entries)),
            files=self._files.snapshot(keys),
            children=self.children,
            parent=self.parent,
        )

    @property
    def usage(self) -> ModelUsage:
        return self._usage if self._usage is not None else ModelUsage(0)

    @property
    def children(self) -> tuple[ChildSession, ...]:
        return self._children.snapshot()

    @property
    def parent(self) -> SubagentLinkedEvent | None:
        return self._children.parent

    @property
    def tree_usage(self) -> ModelUsage:
        """Local usage plus recorded child receipts, including their descendants."""
        return sum_usage((self.usage, *(child.usage for child in self.children)))

    def _release_turn(self) -> None:
        """Release execution buffers after publishing the terminal result."""
        self._events.clear()
        self._turn_state = None
        self._turn_seed = ()
        self._snapshot = None
        self._turns.release_closed()
        self._requests.clear()
        self._requests_by_id.clear()
        self._finished_requests.clear()
        self._request_snapshot = None
        grants = {key for values in self._grants.values() for key in values}
        self._approval_records = {
            key: record
            for key, record in self._approval_records.items()
            if key in grants or record.resolution is None
        }
        self._approvals_by_call.clear()
        self._approvals = None
        self._retain_resources()

    def _retain_resources(self) -> None:
        entries = self._history.items()
        refs = image_refs(entries)
        refs.update(
            part.asset
            for event in self._pending.values()
            for message in (event.message, *event.context)
            if not isinstance(message.content, str)
            for part in message.content
            if isinstance(part, ImageReference)
        )
        self._assets.retain(refs)
        self._files.retain({ref.path for entry in entries for ref in entry.resources})
        self._images.clear()

    async def subscribe(
        self, *, transient: bool = False, buffer_size: int = 256
    ) -> AsyncIterator[DurableEvent | TransientEvent]:
        """Observe acknowledged records; optionally include live notifications."""
        if self._subscriptions_closed:
            return
        queue: EventBuffer[DurableEvent | TransientEvent | None] = EventBuffer(
            buffer_size, merge=coalesce_deltas
        )
        self._observers[queue] = transient
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            self._observers.pop(queue, None)

    def _notify(self, event: DurableEvent | TransientEvent) -> None:
        for queue, transient in tuple(self._observers.items()):
            if isinstance(event, DurableEvent) or transient:
                if not queue.put(event):
                    self._observers.pop(queue, None)

    def __len__(self) -> int:
        return self._sequence

    def finish_subscriptions(self) -> None:
        """Finish audit subscriptions after dependent plugins have torn down."""
        self._subscriptions_closed = True
        for queue in tuple(self._observers):
            queue.put(None)

    @property
    def uncompacted_turn_count(self) -> int:
        return self._uncompacted_turns

    def last[E: DurableEvent](self, kind: type[E]) -> E | None:
        """Return the last event of an exact registered kind in constant time."""
        indexed = self._last.get(kind)
        return cast(E, indexed[1]) if indexed is not None else None

    def since[E: DurableEvent](self, kind: type[E]) -> tuple[DurableEvent, ...]:
        """Return the suffix after the last event of this exact kind."""
        indexed = self._last.get(kind)
        start = indexed[0] + 1 - (self._sequence - len(self._events)) if indexed else 0
        return tuple(self._events[max(0, start) :])

    @property
    def messages(self) -> tuple[Message, ...]:
        return self._history.snapshot()

    @property
    def requests(self) -> tuple[ModelRequest, ...]:
        if self._request_snapshot is None:
            self._request_snapshot = tuple(self._requests)
        return self._request_snapshot

    def request(self, request_id: str) -> ModelRequest | None:
        """Look up the final submitted request by its identity within the current turn."""
        return self._requests_by_id.get(request_id)

    def included_observations(self, request: ModelRequest) -> tuple[IncludedObservation, ...]:
        """Return provenance of observation messages retained in this request."""
        return self._history.included_observations(request.messages)

    def request_finished(self, request_id: str) -> bool:
        """Whether a reply or failure has closed this current-turn request."""
        return request_id in self._finished_requests

    def execution_pending(self, call_id: str) -> bool:
        """Whether a tool execution start is still missing its receipt."""
        return call_id in self._open_executions

    @property
    def turns(self) -> tuple[Turn, ...]:
        return self._turns.snapshot

    @property
    def open_turn(self) -> Turn | None:
        return self._turns.open_turn

    def current_turn(self) -> Turn:
        """Return the open turn that runtime steps require."""
        turn = self._turns.open_turn
        if turn is None:
            raise SessionStateError("No turn is open")
        return turn

    @property
    def inbox(self) -> Inbox:
        if self._inbox is None:
            self._inbox = Inbox.from_pending(self._pending.values())
        return self._inbox

    @property
    def has_wakeup(self) -> bool:
        return bool(self._pending)

    @property
    def has_step_wakeup(self) -> bool:
        return bool(self._step_pending)

    @property
    def approvals(self) -> ApprovalState:
        if self._approvals is None:
            self._approvals = ApprovalState.from_records(self._approval_records.values())
        return self._approvals

    @property
    def control(self) -> RunControl:
        return self._control

    def approval(self, request_id: str) -> Approval | None:
        return self._approval_records.get(request_id)

    def approval_grant(self, tool: str, *, scope: str) -> Approval | None:
        grants = self._grants.get((scope, tool), {})
        return next(reversed(grants.values()), None)

    def latest_approval(self, call_id: str) -> Approval | None:
        return next(reversed(self._approvals_by_call.get(call_id, {}).values()), None)

    def _index_grant(self, record: Approval) -> None:
        key = (record.request.scope, record.request.call.name)
        grants = self._grants.setdefault(key, {})
        if record.is_grant:
            grants.clear()
            grants[record.request_id] = record
        else:
            grants.pop(record.request_id, None)

    async def append(
        self,
        event: DurableEvent,
        *,
        on_committed: Callable[[], None] | None = None,
        during_run: str | None = None,
    ) -> None:
        self.registry.validate(event)
        cancelled = False
        terminal = self._persistence is not None or isinstance(
            event,
            ToolExecutionFinishedEvent
            | ToolCallFinishedEvent
            | AssistantMessageEvent
            | ModelRequestFailedEvent
            | SubagentFinishedEvent,
        )
        if terminal:
            # An invocation/review already finished. Drain its checkpoint even if
            # abort arrives while another append owns the log lock. Observers stay
            # outside this shield so an unresponsive hook cannot block cancellation.
            checkpoint = asyncio.create_task(self._append(event, during_run=during_run))
            while not checkpoint.done():
                try:
                    await asyncio.shield(checkpoint)
                except asyncio.CancelledError:
                    cancelled = True
            seq, event = checkpoint.result()
        else:
            seq, event = await self._append(event, during_run=during_run)
        if cancelled and terminal:
            raise asyncio.CancelledError
        if on_committed is not None:
            on_committed()
        try:
            if self.scope is not None:
                await self.scope.dispatch(SessionAppended(self, event, seq))
                await self.scope.dispatch(event)
        finally:
            if isinstance(event, TurnEndedEvent):
                self._release_turn()
        if cancelled:
            raise asyncio.CancelledError

    async def _append(
        self,
        event: DurableEvent,
        *,
        during_run: str | None = None,
    ) -> tuple[int, DurableEvent]:
        async with self._lock:
            self.registry.validate(event)
            if self._persistence is not None and not self._restoring:
                self._persistence.check()
            event = await self._images.retain_event(event)
            if during_run is not None:
                if not isinstance(event, InboxAddedEvent) or event.mode is not InboxMode.STEER:
                    raise ValueError("A run precondition requires steering input")
                turn = self.open_turn
                pending = self._pending.get(during_run)
                if not (
                    (turn is not None and turn.start.input_id == during_run)
                    or (pending is not None and pending.mode is InboxMode.FOLLOWUP)
                ):
                    raise SessionStateError("The target run has already finished")
            if isinstance(event, InboxAddedEvent) and event.message.id in self._pending:
                raise ValueError(f"Duplicate pending message id: {event.message.id}")
            if isinstance(event, InboxRemovedEvent) and event.message_id not in self._pending:
                raise ValueError(f"Removing unknown inbox message: {event.message_id}")
            approval = approval_transition(self._approval_records, event)
            self._turns.validate(event)
            self._children.validate(event)
            seq = self._sequence
            if isinstance(event, TurnStartedEvent):
                self._turn_state = self.snapshot()
                self._turn_seed = self._checkpoint_seed()
                self._turn_offset = seq
            if isinstance(event, CompactionAppliedEvent):
                if not self._compacted_through < event.through <= seq or not event.summary.strip():
                    raise ValueError("Compaction must advance a completed context boundary")
                boundaries = {boundary for boundary, _ in self._turn_boundaries}
                boundaries.update(
                    self.event_offset + index + 1
                    for index, item in enumerate(self._events)
                    if isinstance(item, StepEndedEvent) and item.reason is StepOutcome.COMPLETED
                )
                if event.through not in boundaries:
                    raise ValueError("Compaction must end at a completed step or closed turn")
                if not set((*event.preserve_turns, *event.retained_turns)) <= self._known_turns:
                    raise ValueError("Compaction cannot revive unavailable turns")
                pending_calls: set[str] = set()
                for entry in sorted(self._history.items(), key=lambda entry: entry.position):
                    if entry.position >= event.through:
                        continue
                    if isinstance(entry.message, AssistantMessage):
                        pending_calls.update(call.id for call in entry.message.tool_calls)
                    elif isinstance(entry.message, ToolMessage):
                        pending_calls.discard(entry.message.tool_call_id)
                if pending_calls:
                    raise ValueError("Compaction must not split unanswered tool calls")
            self._sequence += 1
            self._events.append(event)
            self._last[type(event)] = (seq, event)
            self._snapshot = None
            self._turns.apply(event)
            self._children.apply(event)
            if isinstance(event, TurnStartedEvent):
                self._known_turns.add(event.turn_id)
            elif isinstance(event, TurnEndedEvent):
                self._turn_boundaries.append((seq + 1, event.turn_id))
                self._closed_count += 1
                self._uncompacted_turns += 1
            self._history.apply(event, seq)
            if isinstance(event, CompactionAppliedEvent):
                self._compacted_through = event.through
                self._preserved_turns = event.preserve_turns
                self._uncompacted_turns = 0
                active = {entry.turn_id for entry in self._history.items()}
                self._turn_boundaries = [
                    (boundary, turn) for boundary, turn in self._turn_boundaries if turn in active
                ]
                self._known_turns = {turn for _, turn in self._turn_boundaries}
                if self.open_turn is not None:
                    self._known_turns.add(self.open_turn.id)
                self._last = {
                    kind: record
                    for kind, record in self._last.items()
                    if kind in (CompactionAppliedEvent, SessionControlChangedEvent)
                }
                if not self._restoring:
                    self._retain_resources()
            if isinstance(event, ToolExecutionStartedEvent):
                self._open_executions.add(event.call_id)
            elif isinstance(event, ToolExecutionFinishedEvent):
                self._open_executions.discard(event.call_id)
            if isinstance(event, InboxAddedEvent):
                self._pending[event.message.id] = event
                if event.target == InboxTarget.NEXT_STEP:
                    self._step_pending.add(event.message.id)
                self._inbox = None
            elif isinstance(event, InboxRemovedEvent):
                del self._pending[event.message_id]
                self._step_pending.discard(event.message_id)
                self._inbox = None
            elif isinstance(event, UserMessageEvent | ContextMessageEvent):
                if self._pending.pop(event.message.id, None) is not None:
                    self._step_pending.discard(event.message.id)
                    self._inbox = None
            if approval is not None:
                self._approval_records[approval.request_id] = approval
                self._index_grant(approval)
                self._approvals_by_call.setdefault(approval.request.call.id, {})[
                    approval.request_id
                ] = approval
                self._approvals = None
            if isinstance(event, SessionControlChangedEvent):
                self._control = RunControl((event,))
            if isinstance(event, ModelRequestedEvent):
                self._requests.append(event.request)
                self._requests_by_id[event.request_id] = event.request
                self._request_snapshot = None
            elif isinstance(event, AssistantMessageEvent | ModelRequestFailedEvent):
                self._finished_requests.add(event.request_id)
            elif isinstance(event, ModelUsageRecordedEvent):
                self._usage = (
                    event.usage if self._usage is None else sum_usage((self._usage, event.usage))
                )
            self.registry.validate(event)
            if (
                self._persistence is not None
                and not self._restoring
                and self._persistence.record(SessionChange(seq, self._checkpoint_entry(event)))
            ):
                await self._persistence.flush(self._checkpoint)
            if not self._restoring:
                self._notify(event)
        return seq, event

    async def publish(self, event: TransientEvent) -> None:
        """Deliver a live event without persistence or projection changes."""
        if not isinstance(event, TransientEvent):
            raise TypeError("publish requires a TransientEvent")
        self.registry.validate(event)
        self._notify(event)
        if self.scope is not None:
            await self.scope.dispatch(SessionPublished(self, event))
            await self.scope.dispatch(event)

    async def emit(self, event: DurableEvent | TransientEvent) -> None:
        """Admit an execution record or publish a live notification through the same catalog."""
        if isinstance(event, DurableEvent):
            await self.append(event)
        elif isinstance(event, TransientEvent):
            await self.publish(event)
        else:
            raise TypeError("emit requires a durable or transient event")

    async def repair_interrupted(self, *, children: bool = True) -> None:
        """Close unfinished activity and answer tool calls after cancellation or resume."""
        async with self._repair_lock:
            await self._repair_interrupted(children=children)

    async def _repair_interrupted(self, *, children: bool) -> None:
        control = self.control
        for child in self.children:
            if children and child.status is SubagentStatus.RUNNING:
                await self.append(
                    SubagentFinishedEvent(
                        child.id,
                        SubagentStatus.INTERRUPTED,
                        "Delegation interrupted; inspect the child log before retrying",
                    )
                )
        for request_id in self._requests_by_id:
            if request_id not in self._finished_requests:
                await self.append(
                    ModelRequestFailedEvent(
                        request_id,
                        ModelRequestOutcome.CANCELLED,
                        "Request interrupted before completion",
                    )
                )
        for approval in self.approvals.pending:
            await self.append(
                ApprovalResolvedEvent(approval.request_id, status=ApprovalStatus.INTERRUPTED)
            )
        turn, step = None, None
        unanswered: dict[str, ToolCall] = {}
        started = set()
        executed: set[str] = set()
        cancelled_executions: set[str] = set()
        call_batches: dict[str, str] = {}
        broken_batches: set[str] = set()
        for event in self._events:
            match event:
                case TurnStartedEvent(turn_id=turn_id):
                    turn = turn_id
                case StepStartedEvent(step=index):
                    step = index
                case AssistantMessageEvent(message=message):
                    for call in message.tool_calls:
                        call_batches[call.id] = message.id
                        unanswered[call.id] = call
                        started.discard(call.id)
                        executed.discard(call.id)
                case ToolCallFinishedEvent(call_id=call_id, result=result):
                    if result.break_chain and call_id in call_batches:
                        broken_batches.add(call_batches[call_id])
                    unanswered.pop(call_id, None)
                case ToolExecutionStartedEvent(call_id=call_id):
                    started.add(call_id)
                case ToolExecutionFinishedEvent(call_id=call_id, result=result):
                    executed.add(call_id)
                    if result.break_chain and call_id in call_batches:
                        broken_batches.add(call_batches[call_id])
                    if result.status is ToolStatus.CANCELLED:
                        cancelled_executions.add(call_id)
                case StepEndedEvent():
                    step = None
                case TurnEndedEvent():
                    turn = None
        for call_id in sorted(started - executed):
            await self.append(
                ToolExecutionFinishedEvent(
                    call_id,
                    ToolResult(
                        "Tool execution interrupted; external outcome is unknown",
                        ToolStatus.CANCELLED,
                    ),
                )
            )
        for call in unanswered.values():
            if call.id not in started and call_batches.get(call.id) in broken_batches:
                await self.append(
                    ToolCallFinishedEvent(
                        call,
                        ToolResult(
                            "Skipped because an earlier tool ended this action chain",
                            ToolStatus.SKIPPED,
                        ),
                    )
                )
                continue
            if call.id in executed and call.id not in cancelled_executions:
                await self.append(
                    ToolCallFinishedEvent(
                        call,
                        ToolResult.error(
                            "Tool execution finished, but result review was interrupted. "
                            "The raw result is retained in tool.execution.finished for inspection. "
                            "Do not repeat the action solely to recover its result."
                        ),
                    )
                )
                continue
            message = "Tool execution interrupted" if control.paused else "Tool execution aborted"
            if call.id in started:
                message += "; external outcome is unknown. Inspect external state before retrying."
            status = ToolStatus.CANCELLED if call.id in started else ToolStatus.ERROR
            await self.append(ToolCallFinishedEvent(call, ToolResult(message, status)))
        if turn is not None:
            if step is not None:
                await self.append(
                    StepEndedEvent(
                        turn,
                        step,
                        StepOutcome.INTERRUPTED if control.paused else StepOutcome.ABORTED,
                    )
                )
            if not control.paused:
                reason = (
                    TurnOutcome.STOPPED
                    if control.state in (ControlState.STOPPING, ControlState.STOPPED)
                    and step is None
                    else TurnOutcome.ABORTED
                )
                await self.append(TurnEndedEvent(turn, reason))
        if control.state == ControlState.PAUSING:
            await self.append(SessionControlChangedEvent(ControlState.PAUSED))
        if control.stopped and not control.keep_inbox:
            for item in self.inbox.pending:
                await self.append(InboxRemovedEvent(item.message.id, InboxRemovalReason.DISCARDED))
        if control.stopped:
            await self.append(SessionControlChangedEvent(ControlState.ACTIVE))
