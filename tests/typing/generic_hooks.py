from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, assert_type

from harnessed import Plugin, tool
from harnessed.kernel import Context, Next, Waterfall, hook


class Intent[R](Protocol):
    async def stage(self, next_: Next[R]) -> R: ...


@dataclass(frozen=True)
class Perform[R](Waterfall[R]):
    intent: Intent[R]


class ActionStage(Plugin):
    @hook(Perform)
    async def stage[R](self, event: Perform[R], next_: Next[R]) -> R:
        result = await event.intent.stage(next_)
        assert_type(result, R)
        return result


@dataclass(frozen=True)
class FindIntent:
    query: str

    async def stage(self, next_: Next[list[str]]) -> list[str]:
        return [match for match in await next_() if self.query in match]


@dataclass(frozen=True)
class NavigateIntent:
    url: str

    async def stage(self, next_: Next[bool]) -> bool:
        return bool(self.url) and await next_()


class BrowserTools(Plugin):
    @tool
    async def find(self, query: str, limit: int = 10) -> list[str]:
        return [query] * limit

    @tool
    async def navigate(self, url: str, *, wait: bool = True) -> bool:
        return bool(url) and wait

    @tool
    def count(self, matches: list[str]) -> int:
        return len(matches)


async def perform[**P, R](
    ctx: Context,
    intent: Intent[R],
    action: Callable[P, Awaitable[R]],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    async def terminal() -> R:
        return await action(*args, **kwargs)

    return await ctx.dispatch(Perform(intent), default=terminal)


async def consumer(ctx: Context, tools: BrowserTools) -> None:
    assert_type(await tools.find("button", limit=2), list[str])
    assert_type(await tools.navigate("https://example.com", wait=False), bool)
    assert_type(tools.count(["button"]), int)
    assert_type(await perform(ctx, FindIntent("button"), tools.find, "button", limit=2), list[str])
    assert_type(
        await perform(ctx, NavigateIntent("url"), tools.navigate, "url", wait=False),
        bool,
    )

    await perform(ctx, FindIntent("button"), tools.navigate, "url")  # ty: ignore[invalid-argument-type]
    await perform(ctx, NavigateIntent("url"), tools.find, "button")  # ty: ignore[invalid-argument-type]
    await perform(ctx, FindIntent("button"), tools.find, 42)  # ty: ignore[invalid-argument-type]
    await perform(ctx, FindIntent("button"), tools.find, "button", limit="two")  # ty: ignore[invalid-argument-type]
    await perform(ctx, NavigateIntent("url"), tools.navigate, "url", wait="yes")  # ty: ignore[invalid-argument-type]
