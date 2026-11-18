from collections.abc import AsyncIterator

from harnessed.events import DurableEvent, TransientEvent
from harnessed.events.stream import coalesce_deltas
from harnessed.runtime.buffering import EventBuffer


class LogObservers:
    """Fan acknowledged records, and optionally live notifications, out to subscribers.

    A subscriber whose bounded buffer overflows is dropped; finishing ends every
    subscription and rejects new ones.
    """

    def __init__(self) -> None:
        self._queues: dict[EventBuffer[DurableEvent | TransientEvent | None], bool] = {}
        self._finished = False

    def __len__(self) -> int:
        return len(self._queues)

    async def subscribe(
        self, *, transient: bool, buffer_size: int
    ) -> AsyncIterator[DurableEvent | TransientEvent]:
        if self._finished:
            return
        queue: EventBuffer[DurableEvent | TransientEvent | None] = EventBuffer(
            buffer_size, merge=coalesce_deltas
        )
        self._queues[queue] = transient
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            self._queues.pop(queue, None)

    def notify(self, event: DurableEvent | TransientEvent) -> None:
        for queue, transient in tuple(self._queues.items()):
            if (isinstance(event, DurableEvent) or transient) and not queue.put(event):
                self._queues.pop(queue, None)

    def finish(self) -> None:
        self._finished = True
        for queue in tuple(self._queues):
            queue.put(None)
