from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from harnessed.events import (
    DurableEvent,
    InboxAddedEvent,
    InboxRemovedEvent,
    InboxTarget,
    MessageAppendedEvent,
    UserMessageEvent,
)


@dataclass(frozen=True, init=False)
class Inbox:
    pending: tuple[InboxAddedEvent, ...]

    @classmethod
    def from_pending(cls, pending: Iterable[InboxAddedEvent]) -> Self:
        snapshot = object.__new__(cls)
        object.__setattr__(snapshot, "pending", tuple(pending))
        return snapshot

    def __init__(self, events: Iterable[DurableEvent]) -> None:
        pending: dict[str, InboxAddedEvent] = {}
        for event in events:
            if isinstance(event, InboxAddedEvent):
                if event.message.id in pending:
                    raise ValueError(f"Duplicate pending message id: {event.message.id}")
                pending[event.message.id] = event
            elif isinstance(event, InboxRemovedEvent):
                if event.message_id not in pending:
                    raise ValueError(f"Removing unknown inbox message: {event.message_id}")
                del pending[event.message_id]
            elif isinstance(event, UserMessageEvent | MessageAppendedEvent):
                pending.pop(event.message.id, None)
        object.__setattr__(self, "pending", tuple(pending.values()))

    @property
    def next_turn(self) -> tuple[InboxAddedEvent, ...]:
        return tuple(item for item in self.pending if item.target == InboxTarget.NEXT_TURN)

    @property
    def next_step(self) -> tuple[InboxAddedEvent, ...]:
        return tuple(item for item in self.pending if item.target == InboxTarget.NEXT_STEP)

    @property
    def has_wakeup(self) -> bool:
        return bool(self.pending)

    @property
    def has_step_wakeup(self) -> bool:
        return bool(self.next_step)

    def for_step(self, step: int) -> tuple[InboxAddedEvent, ...]:
        if type(step) is not int or step < 1:
            raise ValueError("Step numbers start at one")
        return (*self.next_turn[:1], *self.next_step) if step == 1 else self.next_step


class InboxProjection:
    """Index queued input by message id and cache its immutable Inbox snapshot."""

    def __init__(self) -> None:
        self._pending: dict[str, InboxAddedEvent] = {}
        self._step_pending: set[str] = set()
        self._snapshot: Inbox | None = Inbox(())

    @property
    def snapshot(self) -> Inbox:
        if self._snapshot is None:
            self._snapshot = Inbox.from_pending(self._pending.values())
        return self._snapshot

    @property
    def pending(self) -> tuple[InboxAddedEvent, ...]:
        return tuple(self._pending.values())

    @property
    def has_wakeup(self) -> bool:
        return bool(self._pending)

    @property
    def has_step_wakeup(self) -> bool:
        return bool(self._step_pending)

    def get(self, message_id: str) -> InboxAddedEvent | None:
        return self._pending.get(message_id)

    def queued(self, message_id: str) -> bool:
        return message_id in self._pending

    def validate(self, event: DurableEvent) -> None:
        if isinstance(event, InboxAddedEvent) and event.message.id in self._pending:
            raise ValueError(f"Duplicate pending message id: {event.message.id}")
        if isinstance(event, InboxRemovedEvent) and event.message_id not in self._pending:
            raise ValueError(f"Removing unknown inbox message: {event.message_id}")

    def apply(self, event: DurableEvent) -> None:
        if isinstance(event, InboxAddedEvent):
            self._pending[event.message.id] = event
            if event.target == InboxTarget.NEXT_STEP:
                self._step_pending.add(event.message.id)
            self._snapshot = None
        elif isinstance(event, InboxRemovedEvent):
            del self._pending[event.message_id]
            self._step_pending.discard(event.message_id)
            self._snapshot = None
        elif isinstance(event, UserMessageEvent | MessageAppendedEvent):
            if self._pending.pop(event.message.id, None) is not None:
                self._step_pending.discard(event.message.id)
                self._snapshot = None
