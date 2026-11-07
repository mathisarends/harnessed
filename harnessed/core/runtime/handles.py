import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Self

from harnessed.core.errors import SessionStateError
from harnessed.core.events.inbox import InboxAddedEvent
from harnessed.core.events.stream import StreamItem
from harnessed.core.model import OutputSchema
from harnessed.core.result import RunResult

type RunEvents[T] = Callable[
    [str, Callable[[], Awaitable[RunResult[T]]]], AsyncGenerator[StreamItem[T], None]
]


class RunHandle[T]:
    """One acknowledged run with an independent result and explicit cancellation.

    Handles opened by session.stream() also iterate live events inside that
    context. Closing their observation never cancels execution; cancel() does.
    Cancelling a result waiter never cancels the session-owned run.
    """

    def __init__(
        self,
        input_id: str,
        completion: asyncio.Future[RunResult[T] | Exception],
        *,
        cancel: Callable[[Self], Awaitable[None]],
        events: RunEvents[T] | None = None,
    ) -> None:
        self.id = input_id
        self._completion = completion
        self._cancel = cancel
        self._cancellation: asyncio.Task[Exception | None] | None = None
        self._events = events(input_id, self.result) if events is not None else None

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> StreamItem[T]:
        if self._events is None:
            raise SessionStateError("Live events require a handle opened by session.stream()")
        return await anext(self._events)

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

    async def aclose(self) -> None:
        """Close event observation without cancelling this run."""
        if self._events is not None:
            await self._events.aclose()

    async def _request_cancellation(self) -> Exception | None:
        try:
            await self._cancel(self)
        except Exception as error:
            # A disconnected waiter must not leave an unobserved task exception.
            return error
        return None


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
        self.handle = RunHandle(
            self.input.message.id, self._completion, cancel=self.cancel, events=self.events
        )

    def finish(self, value: RunResult[Any] | Exception) -> None:
        if not self._completion.done():
            # Store errors as values so an intentionally unobserved handle does
            # not generate an unhandled Future exception warning.
            self._completion.set_result(value)
