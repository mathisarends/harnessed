import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable
from functools import partial
from typing import cast

from harnessed.errors import RunUnavailable, SessionFormatError, SessionStateError
from harnessed.events import (
    ApprovalResolvedEvent,
    ApprovalStatus,
    AssistantMessageEvent,
    CompactionAppliedEvent,
    ControlState,
    DurableEvent,
    InboxAddedEvent,
    InboxMode,
    InboxRemovalReason,
    InboxRemovedEvent,
    IncludedObservation,
    LogEvent,
    MessageAppendedEvent,
    ModelRequestFailedEvent,
    ModelRequestOutcome,
    SessionControlChangedEvent,
    StepEndedEvent,
    StepOutcome,
    SubagentFinishedEvent,
    SubagentLinkedEvent,
    SubagentStatus,
    ToolCallFinishedEvent,
    ToolExecutionFinishedEvent,
    TransientEvent,
    TurnEndedEvent,
    TurnStartedEvent,
)
from harnessed.events.registry import EventRegistry
from harnessed.events.unions import SESSION_EVENT_TYPES
from harnessed.kernel import Scope
from harnessed.models.assets import AssetStore, MemoryAssets
from harnessed.models.messages import (
    AssistantMessage,
    ImageReference,
    Message,
    SpillRef,
    ToolMessage,
    UserMessage,
)
from harnessed.models.model import ModelRequest, ModelUsage, sum_usage
from harnessed.runtime.approval_policy import Approval, ApprovalState
from harnessed.runtime.hooks import SessionAppended

from .approvals import ApprovalProjection
from .checkpoint import (
    CheckpointEntry,
    CheckpointExtension,
    CheckpointSeed,
    SessionCheckpoint,
)
from .children import ChildProjection, ChildSession
from .commit import SessionChange
from .control import RunControl
from .files import MemoryFiles
from .history import WorkingHistory
from .history import derive_messages as derive_messages
from .history import derive_requests as derive_requests
from .images import SessionImages
from .inbox import Inbox, InboxProjection
from .observers import LogObservers
from .publishing import SessionPublished
from .repair import InterruptedActivity
from .requests import RequestProjection
from .state import (
    HistoryEntry,
    HistoryStep,
    HistoryTurn,
    SessionState,
    image_refs,
)
from .store import SessionPersistence
from .turns import Turn, TurnProjection


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
        assets: MemoryAssets | None = None,
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
        self._assets = assets if assets is not None else MemoryAssets(state.assets if state else ())
        self._images = SessionImages(self._assets)
        self._observers = LogObservers()
        self._lock = asyncio.Lock()
        self._repair_lock = asyncio.Lock()
        self._inbox = InboxProjection()
        self._approvals = ApprovalProjection()
        self._control = RunControl(())
        self._requests = RequestProjection()
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
                *(step.boundary for step in state.steps),
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
        events: list[CheckpointSeed] = list(self._inbox.pending)
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
        self._assets.clear()
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

    async def retain_message[M: Message](self, message: M) -> M:
        """Move inline image payloads of a new message into this log's assets."""
        return await self._images.retain_message(message)

    async def retain_request(self, request: ModelRequest) -> ModelRequest:
        """Move inline image payloads of a model request into this log's assets."""
        return await self._images.retain_request(request)

    @property
    def completed_steps(self) -> tuple[HistoryStep, ...]:
        """Closed steps of the working history, oldest first."""
        return tuple(self._history.completed_steps)

    def history_entries(self) -> tuple[HistoryEntry, ...]:
        """Entries of the working history in conversation order."""
        return self._history.items()

    @property
    def events(self) -> tuple[DurableEvent, ...]:
        if self._snapshot is None:
            self._snapshot = tuple(self._events)
        return self._snapshot

    @property
    def closed_turn_count(self) -> int:
        return self._closed_count

    @property
    def plugin_instructions(self) -> dict[str, str]:
        return self._history.plugin_instructions.copy()

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
            plugin_instructions=self.plugin_instructions,
            turns=tuple(HistoryTurn(turn, boundary) for boundary, turn in self._turn_boundaries),
            steps=tuple(self._history.completed_steps),
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
        return self._requests.usage

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
        self._requests.release_turn()
        self._approvals.release_turn()
        self._retain_resources()

    def _retain_resources(self) -> None:
        entries = self._history.items()
        refs = image_refs(entries)
        refs.update(
            part.asset
            for event in self._inbox.pending
            for message in (event.message,)
            if not isinstance(message.content, str)
            for part in message.content
            if isinstance(part, ImageReference)
        )
        self._assets.retain(refs)
        paths = {ref.path for entry in entries for ref in entry.resources}
        self._files.retain(paths)
        self._images.clear()

    def subscribe(
        self, *, transient: bool = False, buffer_size: int = 256
    ) -> AsyncIterator[DurableEvent | TransientEvent]:
        """Observe acknowledged records; optionally include live notifications."""
        return self._observers.subscribe(transient=transient, buffer_size=buffer_size)

    def __len__(self) -> int:
        return self._sequence

    def finish_subscriptions(self) -> None:
        """Finish audit subscriptions after dependent plugins have torn down."""
        self._observers.finish()

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
        return self._requests.requests

    def request(self, request_id: str) -> ModelRequest | None:
        """Look up the final submitted request by its identity within the current turn."""
        return self._requests.request(request_id)

    def included_observations(self, request: ModelRequest) -> tuple[IncludedObservation, ...]:
        """Return provenance of observation messages retained in this request."""
        return self._history.included_observations(request.messages)

    def request_finished(self, request_id: str) -> bool:
        """Whether a reply or failure has closed this current-turn request."""
        return self._requests.finished(request_id)

    def execution_pending(self, call_id: str) -> bool:
        """Whether a tool execution start is still missing its receipt."""
        return self._requests.execution_pending(call_id)

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
        return self._inbox.snapshot

    @property
    def has_wakeup(self) -> bool:
        return self._inbox.has_wakeup

    @property
    def has_step_wakeup(self) -> bool:
        return self._inbox.has_step_wakeup

    @property
    def approvals(self) -> ApprovalState:
        return self._approvals.state

    @property
    def control(self) -> RunControl:
        return self._control

    def approval(self, request_id: str) -> Approval | None:
        return self._approvals.record(request_id)

    def approval_grant(self, tool: str, *, scope: str) -> Approval | None:
        return self._approvals.grant(tool, scope=scope)

    def latest_approval(self, call_id: str) -> Approval | None:
        return self._approvals.latest(call_id)

    async def append(
        self,
        event: DurableEvent,
        *,
        on_committed: Callable[[], None] | None = None,
        during_run: str | None = None,
        expected_turn: str | None = None,
        expected_input: InboxAddedEvent | None = None,
    ) -> None:
        """Admit an event and commit it before notifying observers.

        Expected turn/input conditions are checked under the admission lock;
        a stale target raises RunUnavailable without recording the event.
        """
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
            checkpoint = asyncio.create_task(
                self._append(
                    event,
                    during_run=during_run,
                    expected_turn=expected_turn,
                    expected_input=expected_input,
                )
            )
            while not checkpoint.done():
                try:
                    await asyncio.shield(checkpoint)
                except asyncio.CancelledError:
                    cancelled = True
            seq, event = checkpoint.result()
        else:
            seq, event = await self._append(
                event,
                during_run=during_run,
                expected_turn=expected_turn,
                expected_input=expected_input,
            )
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
        expected_turn: str | None = None,
        expected_input: InboxAddedEvent | None = None,
    ) -> tuple[int, DurableEvent]:
        async with self._lock:
            self.registry.validate(event)
            if self._persistence is not None and not self._restoring:
                self._persistence.check()
            # Additive input must not claim an acknowledged run's queued message.
            # Check under the admission lock, before retaining images or recording state.
            if (
                isinstance(event, MessageAppendedEvent)
                and isinstance(event.message, UserMessage)
                and self._inbox.queued(event.message.id)
            ):
                raise ValueError(
                    f"Cannot append a user message with a queued inbox id: {event.message.id}"
                )
            event = await self._images.retain_event(event)
            self._check_preconditions(
                event,
                during_run=during_run,
                expected_turn=expected_turn,
                expected_input=expected_input,
            )
            self._inbox.validate(event)
            approval = self._approvals.transition(event)
            self._turns.validate(event)
            self._children.validate(event)
            seq = self._sequence
            if isinstance(event, TurnStartedEvent):
                self._turn_state = self.snapshot()
                self._turn_seed = self._checkpoint_seed()
                self._turn_offset = seq
            if isinstance(event, CompactionAppliedEvent):
                self._validate_compaction(event, seq)
            self._project(event, seq, approval)
            self.registry.validate(event)
            if (
                self._persistence is not None
                and not self._restoring
                and self._persistence.record(SessionChange(seq, self._checkpoint_entry(event)))
            ):
                await self._persistence.flush(self._checkpoint)
            if not self._restoring:
                self._observers.notify(event)
        return seq, event

    async def publish(self, event: TransientEvent) -> None:
        """Deliver a live event without persistence or projection changes."""
        if not isinstance(event, TransientEvent):
            raise TypeError("publish requires a TransientEvent")
        self.registry.validate(event)
        self._observers.notify(event)
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

    def _check_preconditions(
        self,
        event: DurableEvent,
        *,
        during_run: str | None,
        expected_turn: str | None,
        expected_input: InboxAddedEvent | None,
    ) -> None:
        if expected_turn is not None:
            turn = self.open_turn
            if turn is None or turn.id != expected_turn:
                raise RunUnavailable("The target turn has already finished")
        if (
            expected_input is not None
            and self._inbox.get(expected_input.message.id) != expected_input
        ):
            raise RunUnavailable("The target input is no longer queued")
        if during_run is not None:
            if not isinstance(event, InboxAddedEvent) or event.mode is not InboxMode.STEER:
                raise ValueError("A run precondition requires steering input")
            turn = self.open_turn
            pending = self._inbox.get(during_run)
            if not (
                (turn is not None and turn.start.input_id == during_run)
                or (pending is not None and pending.mode is InboxMode.FOLLOWUP)
            ):
                raise SessionStateError("The target run has already finished")

    def _validate_compaction(self, event: CompactionAppliedEvent, seq: int) -> None:
        if not self._compacted_through < event.through <= seq or not event.summary.strip():
            raise ValueError("Compaction must advance a completed context boundary")
        boundaries = {boundary for boundary, _ in self._turn_boundaries}
        boundaries.update(step.boundary for step in self._history.completed_steps)
        boundaries.update(
            self.event_offset + index + 1
            for index, item in enumerate(self._events)
            if isinstance(item, StepEndedEvent) and item.reason is StepOutcome.COMPLETED
        )
        if event.through not in boundaries:
            raise ValueError("Compaction must end at a completed step or closed turn")
        if not {*event.preserve_turns, *event.retained_turns} <= self._known_turns:
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

    def _project(self, event: DurableEvent, seq: int, approval: Approval | None) -> None:
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
            self._apply_compaction(event)
        self._requests.apply(event)
        self._inbox.apply(event)
        if approval is not None:
            self._approvals.apply(approval)
        if isinstance(event, SessionControlChangedEvent):
            self._control = RunControl((event,))

    def _apply_compaction(self, event: CompactionAppliedEvent) -> None:
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
        for request_id in self._requests.unfinished():
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
        for record in InterruptedActivity.scan(self._events).repairs(control):
            await self.append(record)
        if control.state == ControlState.PAUSING:
            await self.append(SessionControlChangedEvent(ControlState.PAUSED))
        if control.stopped and not control.keep_inbox:
            for item in self.inbox.pending:
                await self.append(InboxRemovedEvent(item.message.id, InboxRemovalReason.DISCARDED))
        if control.stopped:
            await self.append(SessionControlChangedEvent(ControlState.ACTIVE))
