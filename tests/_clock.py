import asyncio
import math


class AdvancingClock:
    """Skip elapsed time while retaining real-time progress for local deadlines."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._real_time = loop.time
        self._offset = 0.0

    def time(self) -> float:
        return self._real_time() + self._offset

    async def advance(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Clock advances must be finite and non-negative")
        self._offset += seconds
        checkpoint = asyncio.Event()
        # Older expired deadlines run before this checkpoint resumes the caller.
        self._loop.call_later(0, checkpoint.set)
        await checkpoint.wait()
