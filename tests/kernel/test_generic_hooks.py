from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from harnessed.kernel import Context, Next, PluginBase, Waterfall, hook


class Intent[R](Protocol):
    async def stage(self, next_: Next[R]) -> R: ...


@dataclass(frozen=True)
class Perform[R](Waterfall[R]):
    intent: Intent[R]


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


async def test_generic_hook_keeps_independent_action_signatures_and_results() -> None:
    seen: list[str] = []
    calls: list[tuple[str, int] | tuple[str, bool]] = []

    class Actions(PluginBase):
        @hook(Perform)
        async def stage[R](self, event: Perform[R], next_: Next[R]) -> R:
            return await event.intent.stage(next_)

    async def find(query: str, limit: int = 10) -> list[str]:
        calls.append((query, limit))
        return [query, "other"][:limit]

    async def navigate(url: str, *, wait: bool = True) -> bool:
        calls.append((url, wait))
        return wait

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

    async with Context() as ctx:
        await ctx.plugin(Actions())

        @ctx.hook(Perform)
        async def observe[R](event: Perform[R], next_: Next[R]) -> R:
            seen.append("before")
            result = await next_()
            seen.append("after")
            return result

        find_intent = FindIntent("button")
        navigate_intent = NavigateIntent("https://example.com")
        assert await perform(ctx, find_intent, find, "button", limit=2) == ["button"]
        assert (
            await perform(ctx, navigate_intent, navigate, "https://example.com", wait=False)
            is False
        )
        assert await perform(ctx, NavigateIntent(""), navigate, "unused") is False

    assert calls == [("button", 2), ("https://example.com", False)]
    assert seen == ["before", "after", "before", "after"]
