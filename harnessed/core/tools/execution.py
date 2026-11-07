import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from types import TracebackType
from typing import Self


class ToolExecutionBudget:
    """A tool's execution deadline, suspended while its task awaits approval.

    Child tasks inherit context variables, but cannot suspend their parent's
    budget: the parent may still be doing work concurrently.
    """

    def __init__(self, timeout: float | None) -> None:
        self._timer = asyncio.timeout(timeout)
        self._pause_depth = 0

    async def __aenter__(self) -> Self:
        await self._timer.__aenter__()
        self._task = asyncio.current_task()
        self._token = _CURRENT_BUDGET.set(self)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            return await self._timer.__aexit__(exc_type, exc, traceback)
        finally:
            _CURRENT_BUDGET.reset(self._token)

    @contextmanager
    def suspended(self) -> Generator[None]:
        if self._task is not asyncio.current_task() or self._timer.expired():
            yield
            return
        remaining = None
        deadline = self._timer.when()
        if self._pause_depth == 0 and deadline is not None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                yield
                return
            self._timer.reschedule(None)
        self._pause_depth += 1
        try:
            yield
        finally:
            self._pause_depth -= 1
            if remaining is not None:
                self._timer.reschedule(asyncio.get_running_loop().time() + remaining)


@contextmanager
def suspend_tool_execution() -> Generator[None]:
    """Exclude an approval wait from the current task's tool execution budget."""
    budget = _CURRENT_BUDGET.get()
    if budget is None:
        yield
    else:
        with budget.suspended():
            yield


_CURRENT_BUDGET: ContextVar[ToolExecutionBudget | None] = ContextVar(
    "harnessed_tool_execution_budget", default=None
)
