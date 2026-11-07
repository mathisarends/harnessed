import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Self

from harnessed.core.events.inbox import InboxAddedEvent
from harnessed.core.events.stream import StreamItem
from harnessed.core.model import OutputSchema
from harnessed.core.result import RunResult

type RunEvents[T] = Callable[
    [str, Callable[[], Awaitable[RunResult[T]]]], AsyncGenerator[StreamItem[T], None]
]


class RunHandle[T]:
    """One acknowledged run with an independent result and explicit cancellation.

    Cancelling a result waiter never cancels the session-owned run. Live event
    observation belongs to RunStream, opened by session.stream().
    """

    def __init__(
        self,
        input_id: str,
        completion: asyncio.Future[RunResult[T] | Exception],
        *,
        cancel: Callable[[Self], Awaitable[None]],
    ) -> None:
        self.id = input_id
        self._completion = completion
        self._cancel = cancel
        self._cancellation: asyncio.Task[Exception | None] | None = None

    def done(self) -> bool:
        return self._completion.done()

    async def result(self) -> RunResult[T]:
        value = await asyncio.shield(self._completion)
        if isinstance(value, Exception):
            raise value
        return value

    async def cancel(self) -> None:
        """Discard this queued run or abort its active turn, preserving other runs.

        Completed runs are unaffected. A queued cancellation returns DISCARDED;
        an interrupted active turn returns ABORTED through result().
        """
        if self._cancellation is None:
            if self.done():
                return
            self._cancellation = asyncio.create_task(self._request_cancellation())
        error = await asyncio.shield(self._cancellation)
        if error is not None:
            raise error

    async def _request_cancellation(self) -> Exception | None:
        try:
            await self._cancel(self)
        except Exception as error:
            # A disconnected waiter must not leave an unobserved task exception.
            return error
        return None


class RunStream[T](RunHandle[T]):
    """One run with a single-consumer live observation owned by session.stream().

    Closing observation never cancels execution; cancel() does. The run's result
    remains available independently of event consumption and context exit.
    """

    def __init__(
        self,
        input_id: str,
        completion: asyncio.Future[RunResult[T] | Exception],
        *,
        cancel: Callable[[RunHandle[T]], Awaitable[None]],
        events: RunEvents[T],
    ) -> None:
        super().__init__(input_id, completion, cancel=cancel)
        self._events = events(input_id, self.result)

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> StreamItem[T]:
        return await anext(self._events)

    async def aclose(self) -> None:
        """Close event observation without cancelling this run."""
        await self._events.aclose()


@dataclass
class SubmittedRun:
    """Session-side state of one submitted run; only it completes the handle."""

    input: InboxAddedEvent
    schema: OutputSchema | None
    cancel: Callable[[RunHandle[Any]], Awaitable[None]]
    events: RunEvents[Any] | None = None
    turn_id: str | None = None
    handle: RunHandle[Any] = field(init=False)
    _completion: asyncio.Future[RunResult[Any] | Exception] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._completion = asyncio.get_running_loop().create_future()
        if self.events is None:
            self.handle = RunHandle(self.input.message.id, self._completion, cancel=self.cancel)
        else:
            self.handle = RunStream(
                self.input.message.id, self._completion, cancel=self.cancel, events=self.events
            )

    def finish(self, value: RunResult[Any] | Exception) -> None:
        if not self._completion.done():
            # Store errors as values so an intentionally unobserved handle does
            # not generate an unhandled Future exception warning.
            self._completion.set_result(value)
