from collections.abc import Awaitable, Callable
from typing import assert_type

from harnessed.kernel import Bail, Context, Emit, EventBus, Parallel, Waterfall


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
    ctx: Context, event: Waterfall[R], terminal: Callable[[], R | Awaitable[R]]
) -> R:
    return await ctx.dispatch(event, default=terminal)


async def consumer(ctx: Context, bus: EventBus) -> None:
    assert_type(await ctx.dispatch(Notice()), None)
    assert_type(await ctx.dispatch(ConcurrentNotice()), None)
    assert_type(await ctx.dispatch(Question()), int | None)
    assert_type(await ctx.dispatch(Transform()), str)
    assert_type(await ctx.dispatch(Transform(), default=lambda: "terminal"), str)
    assert_type(await ctx.dispatch(Transform(), default=text), str)
    assert_type(await ctx.dispatch(Choice(), default=text), str | int)
    assert_type(await ctx.dispatch(Choice(), default=number), str | int)
    assert_type(await bus.dispatch(ctx, Transform(), default=lambda: "terminal"), str)
    assert_type(await bus.dispatch(ctx, Transform(), default=text), str)
    assert_type(await forward(ctx, Transform(), text), str)

    await ctx.dispatch(Transform(), default=lambda: 42)  # ty: ignore[invalid-argument-type]
    await ctx.dispatch(Transform(), default=number)  # ty: ignore[invalid-argument-type]
    await ctx.dispatch(Transform(), default=lambda: None)  # ty: ignore[invalid-argument-type]
    await ctx.dispatch(Choice(), default=lambda: None)  # ty: ignore[invalid-argument-type]
    await bus.dispatch(ctx, Transform(), default=lambda: 42)  # ty: ignore[invalid-argument-type]
    await bus.dispatch(ctx, Transform(), default=number)  # ty: ignore[invalid-argument-type]
