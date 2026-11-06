from collections.abc import Awaitable, Callable
from typing import assert_type

from harnessed.kernel import Bail, Emit, EventBus, Parallel, Scope, Waterfall


class Notice(Emit):
    pass


class ConcurrentNotice(Parallel):
    pass


class Question(Bail[int]):
    pass


class Transform(Waterfall[str]):
    pass


class Choice(Waterfall[str | int]):
    pass


async def text() -> str:
    return "terminal"


async def number() -> int:
    return 42


async def forward[R](
    scope: Scope, event: Waterfall[R], terminal: Callable[[], R | Awaitable[R]]
) -> R:
    return await scope.dispatch(event, default=terminal)


async def consumer(scope: Scope, bus: EventBus) -> None:
    assert_type(await scope.dispatch(Notice()), None)
    assert_type(await scope.dispatch(ConcurrentNotice()), None)
    assert_type(await scope.dispatch(Question()), int | None)
    assert_type(await scope.dispatch(Transform()), str)
    assert_type(await scope.dispatch(Transform(), default=lambda: "terminal"), str)
    assert_type(await scope.dispatch(Transform(), default=text), str)
    assert_type(await scope.dispatch(Choice(), default=text), str | int)
    assert_type(await scope.dispatch(Choice(), default=number), str | int)
    assert_type(await bus.dispatch(scope, Transform(), default=lambda: "terminal"), str)
    assert_type(await bus.dispatch(scope, Transform(), default=text), str)
    assert_type(await forward(scope, Transform(), text), str)

    await scope.dispatch(Transform(), default=lambda: 42)  # ty: ignore[invalid-argument-type]
    await scope.dispatch(Transform(), default=number)  # ty: ignore[invalid-argument-type]
    await scope.dispatch(Transform(), default=lambda: None)  # ty: ignore[invalid-argument-type]
    await scope.dispatch(Choice(), default=lambda: None)  # ty: ignore[invalid-argument-type]
    await bus.dispatch(scope, Transform(), default=lambda: 42)  # ty: ignore[invalid-argument-type]
    await bus.dispatch(scope, Transform(), default=number)  # ty: ignore[invalid-argument-type]
