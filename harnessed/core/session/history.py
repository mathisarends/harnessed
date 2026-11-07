from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import TypedDict, Unpack

from harnessed.core.events.base import DurableEvent
from harnessed.core.events.compaction import CompactionAppliedEvent
from harnessed.core.events.messages import (
    AssistantMessageEvent,
    MessageAppendedEvent,
    MessageLifetime,
    PluginInstructionsChangedEvent,
    SystemMessageEvent,
    UserMessageEvent,
)
from harnessed.core.events.model import ModelRequestedEvent
from harnessed.core.events.observations import (
    IncludedObservation,
    ObservationPolicyAppliedEvent,
    ObservationRecordedEvent,
    SpillExpiredEvent,
)
from harnessed.core.events.tools import ToolCallFinishedEvent
from harnessed.core.events.turn import TurnEndedEvent, TurnStartedEvent
from harnessed.core.messages import Message, SpillRef, SystemMessage, ToolMessage
from harnessed.core.model import ModelRequest

from .state import HistoryEntry, SessionState


class WorkingHistory:
    """Retained conversation values, independent of execution-event storage."""

    def __init__(self, state: SessionState | None = None) -> None:
        self._items: dict[int, HistoryEntry] = (
            {entry.position: entry for entry in state.history} if state is not None else {}
        )
        self.plugin_instructions: dict[str, str] = (
            dict(state.plugin_instructions) if state is not None else {}
        )
        self._snapshot: tuple[Message, ...] | None = None
        self._turn: str | None = None
        self.max_observations = state.observation_limit if state is not None else None

    def apply(self, event: DurableEvent, seq: int) -> None:
        self._snapshot = None
        if isinstance(event, TurnStartedEvent):
            self._turn = event.turn_id
        elif isinstance(event, TurnEndedEvent):
            self._items = {
                pos: item
                for pos, item in self._items.items()
                if item.lifetime != MessageLifetime.TURN
            }
            self._turn = None
        elif isinstance(event, SystemMessageEvent):
            self._items = {
                pos: item for pos, item in self._items.items() if item.message.id != "system"
            }
            if event.content:
                self._put(seq, SystemMessage(event.content, id="system"))
        elif isinstance(event, UserMessageEvent | AssistantMessageEvent):
            self._put(seq, event.message)
        elif isinstance(event, ToolCallFinishedEvent):
            self._put(
                seq,
                ToolMessage(
                    event.result.content,
                    tool_call_id=event.call_id,
                    id=event.call_id,
                    status=event.result.status,
                    exit_code=event.result.exit_code,
                ),
                resources=(event.result.spill,) if event.result.spill is not None else (),
            )
        elif isinstance(event, MessageAppendedEvent):
            self._put(seq, event.message, lifetime=event.lifetime)
        elif isinstance(event, PluginInstructionsChangedEvent):
            if event.value is None:
                self.plugin_instructions.pop(event.name, None)
            else:
                self.plugin_instructions[event.name] = event.value
        elif isinstance(event, ObservationRecordedEvent):
            if event.message.content:
                self._put(
                    seq,
                    event.message,
                    observation=IncludedObservation(
                        event.source,
                        event.refs,
                        turn_id=event.turn_id,
                        step=event.step,
                        message_id=event.message.id,
                    ),
                )
            self._limit_observations()
        elif isinstance(event, ObservationPolicyAppliedEvent):
            self.max_observations = event.max_observations
            self._limit_observations()
        elif isinstance(event, CompactionAppliedEvent):
            self._items = {item.position: item for item in self.compacted(event, seq)}
        elif isinstance(event, SpillExpiredEvent):
            self._items = {
                pos: replace(
                    item,
                    resources=tuple(ref for ref in item.resources if ref.path != event.ref.path),
                )
                for pos, item in self._items.items()
            }

    def compacted(self, applied: CompactionAppliedEvent, seq: int) -> tuple[HistoryEntry, ...]:
        preserved = (*applied.preserve_turns, *applied.retained_turns)
        kept = [
            item
            for item in self.items()
            if (
                item.position >= applied.through
                or item.message.id == "system"
                or item.turn_id in preserved
                or item.position in applied.preserve_inputs
            )
            and not item.message.id.startswith("compaction:")
        ]
        resources = tuple(dict.fromkeys(ref for item in self.items() for ref in item.resources))
        kept.append(
            HistoryEntry(
                seq,
                SystemMessage(
                    f"Conversation summary:\n{applied.summary}",
                    id=f"compaction:{applied.id or seq}",
                ),
                resources=resources,
            )
        )
        return tuple(kept)

    def items(self) -> tuple[HistoryEntry, ...]:
        return tuple(
            sorted(
                self._items.values(),
                key=lambda item: (
                    -2
                    if item.message.id == "system"
                    else -1
                    if item.message.id.startswith("compaction:")
                    else item.position
                ),
            )
        )

    def snapshot(self) -> tuple[Message, ...]:
        if self._snapshot is None:
            self._snapshot = tuple(item.message for item in self.items())
        return self._snapshot

    def entries(self) -> tuple[tuple[int, Message], ...]:
        return tuple((item.position, item.message) for item in self.items())

    def origin(self, seq: int) -> str | None:
        item = self._items.get(seq)
        return item.turn_id if item is not None else None

    def included_observations(
        self, messages: tuple[Message, ...]
    ) -> tuple[IncludedObservation, ...]:
        selected = {message.id: message for message in messages}
        return tuple(
            item.observation
            for item in self.items()
            if item.observation is not None and selected.get(item.message.id) == item.message
        )

    def _put(self, seq: int, message: Message, **metadata: Unpack[_EntryMetadata]) -> None:
        self._items[seq] = HistoryEntry(seq, message, self._turn, **metadata)

    def _limit_observations(self) -> None:
        if self.max_observations is None:
            return
        seen: dict[str, int] = {}
        for pos, item in reversed(tuple(self._items.items())):
            if item.observation is None:
                continue
            source = item.observation.source
            seen[source] = seen.get(source, 0) + 1
            if seen[source] > self.max_observations:
                del self._items[pos]


class _EntryMetadata(TypedDict, total=False):
    lifetime: MessageLifetime
    observation: IncludedObservation
    resources: tuple[SpillRef, ...]


def derive_requests(events: Iterable[DurableEvent]) -> tuple[ModelRequest, ...]:
    return tuple(event.request for event in events if isinstance(event, ModelRequestedEvent))


def derive_messages(events: Iterable[DurableEvent]) -> tuple[Message, ...]:
    return tuple(message for _, message in _derive_message_entries(events))


def _derive_message_entries(events: Iterable[DurableEvent]) -> tuple[tuple[int, Message], ...]:
    events = tuple(events)
    active_turn, max_observations = None, None
    observations: dict[str, list[int]] = {}
    for seq, event in enumerate(events):
        match event:
            case TurnStartedEvent(turn_id=turn_id):
                active_turn = turn_id
            case TurnEndedEvent():
                active_turn = None
            case ObservationPolicyAppliedEvent(max_observations=limit):
                max_observations = limit
            case ObservationRecordedEvent(source=source):
                observations.setdefault(source, []).append(seq)
    visible_observations = set()
    for indices in observations.values():
        visible_observations.update(
            indices if max_observations is None else indices[-max_observations:]
        )
    entries: list[tuple[int, Message]] = []
    originals: list[tuple[int, Message]] = []

    def add(seq, message) -> None:
        entries.append((seq, message))
        originals.append((seq, message))

    turn_offsets = {}
    current_turn = None
    for seq, event in enumerate(events):
        if isinstance(event, TurnStartedEvent):
            current_turn = event.turn_id
        turn_offsets[seq] = current_turn
        if isinstance(event, TurnEndedEvent):
            current_turn = None
        match event:
            case SystemMessageEvent(content=content):
                entries = [(offset, m) for offset, m in entries if m.id != "system"]
                if content:
                    entries.insert(0, (seq, SystemMessage(content, id="system")))
            case UserMessageEvent(message=message) | AssistantMessageEvent(message=message):
                add(seq, message)
            case MessageAppendedEvent(message=message, lifetime=lifetime, turn_id=turn_id):
                if lifetime == MessageLifetime.SESSION or turn_id == active_turn:
                    add(seq, message)
            case ObservationRecordedEvent(message=message):
                if message.content and seq in visible_observations:
                    add(seq, message)
            case ToolCallFinishedEvent(call_id=call_id, result=result):
                add(
                    seq,
                    ToolMessage(
                        result.content,
                        tool_call_id=call_id,
                        id=call_id,
                        status=result.status,
                        exit_code=result.exit_code,
                    ),
                )
            case CompactionAppliedEvent(through=through, summary=summary, preserve_turns=preserved):
                preserved = (*preserved, *event.retained_turns)
                entries = [
                    (offset, message)
                    for offset, message in entries
                    if (
                        offset >= through
                        or message.id == "system"
                        or turn_offsets.get(offset) in preserved
                        or offset in event.preserve_inputs
                    )
                    and not message.id.startswith("compaction:")
                ]
                retained_offsets = {offset for offset, _ in entries}
                entries.extend(
                    (offset, message)
                    for offset, message in originals
                    if offset < through
                    and (turn_offsets.get(offset) in preserved or offset in event.preserve_inputs)
                    and offset not in retained_offsets
                )
                entries.sort(key=lambda item: -1 if item[1].id == "system" else item[0])
                position = 1 if entries and entries[0][1].id == "system" else 0
                entries.insert(
                    position,
                    (
                        seq,
                        SystemMessage(
                            f"Conversation summary:\n{summary}",
                            id=f"compaction:{event.id or seq}",
                        ),
                    ),
                )
    return tuple(entries)
