import pytest

from harnessed.core.plugin import Plugin
from harnessed.kernel import (
    Bail,
    Emit,
    Inject,
    Next,
    Parallel,
    PluginBase,
    Scope,
    Waterfall,
    hook,
)


class Ping(Emit):
    pass


class Pong(Emit):
    pass


class Together(Parallel):
    pass


class FirstChoice(Bail[int]):
    pass


class SecondChoice(Bail[int]):
    pass


class FirstWrap(Waterfall[str]):
    pass


class SecondWrap(Waterfall[str]):
    pass


class EmptyWrap(Waterfall[None]):
    pass


@pytest.mark.parametrize("base", [PluginBase, Plugin])
async def test_multi_event_methods_dispatch_with_injection_and_follow_plugin_lifetime(
    base: type[PluginBase],
) -> None:
    seen: list[tuple[Emit | Parallel, int]] = []

    class Recorder(base):
        @hook(Ping, Pong, Together)
        async def forward(self, event: Ping | Pong | Together, value: Inject[int]) -> None:
            seen.append((event, value))

    events = (Ping(), Pong(), Together())
    async with Scope() as scope:
        scope.provide(int, 1)
        recipe = Recorder()
        handle = await scope.plugin(recipe)
        for event in events:
            await scope.dispatch(event)
        await handle.aclose()
        for event in events:
            await scope.dispatch(event)
        scope.provide(int, 2)
        await scope.plugin(recipe)
        for event in events:
            await scope.dispatch(event)
    for event in events:
        await scope.dispatch(event)
    assert seen == [(event, value) for value in (1, 2) for event in events]


async def test_multi_event_priority_prepend_and_stacked_hooks() -> None:
    seen: list[str] = []

    class Recorder(PluginBase):
        @hook(Ping)
        @hook(Pong, Together, priority=-10, prepend=True)
        def forward(self, event: Ping | Pong | Together) -> None:
            seen.append("shared")

    async with Scope() as scope:
        for event in (Ping, Pong):
            scope.on(event, lambda event: seen.append("default"))
            scope.on(event, lambda event: seen.append("early"), priority=-10)
            scope.on(event, lambda event: seen.append("first"), priority=-20)
        await scope.plugin(Recorder())
        await scope.dispatch(Ping())
        assert seen == ["first", "early", "default", "shared"]
        seen.clear()
        await scope.dispatch(Pong())
        assert seen == ["first", "shared", "early", "default"]


async def test_multi_event_bail_and_waterfall_keep_their_dispatch_semantics() -> None:
    class Handlers(PluginBase):
        @hook(FirstChoice, SecondChoice)
        def choose(self, event: FirstChoice | SecondChoice) -> int | None:
            return 0

        @hook(FirstWrap, SecondWrap)
        async def wrap(self, event: FirstWrap | SecondWrap, next_: Next[str]) -> str:
            return "[" + await next_() + "]"

    async with Scope() as scope:
        await scope.plugin(Handlers())
        for choice in (FirstChoice, SecondChoice):
            scope.on(choice, lambda event: pytest.fail("Bail must stop at zero"))
            assert await scope.dispatch(choice()) == 0
        for wrap in (FirstWrap(), SecondWrap()):
            assert await scope.dispatch(wrap, default=lambda: "core") == "[core]"


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_waterfall_terminal_supports_sync_and_async_results(asynchronous: bool) -> None:
    seen: list[str] = []

    def terminal() -> str:
        seen.append("terminal")
        return "core"

    async def async_terminal() -> str:
        return terminal()

    async with Scope() as scope:

        @scope.hook(FirstWrap)
        async def wrap(event: FirstWrap, next_: Next[str]) -> str:
            result = await next_()
            seen.append(result)
            return f"[{result}]"

        default = async_terminal if asynchronous else terminal
        assert await scope.dispatch(FirstWrap(), default=default) == "[core]"
    assert seen == ["terminal", "core"]


def test_multi_event_decorator_preserves_function_identity_and_can_be_reused() -> None:
    def observe(event: Ping | Pong) -> None:
        pass

    async def observe_async(event: Ping | Pong) -> None:
        pass

    decorate = hook(Ping, Pong)
    assert decorate(observe) is observe
    assert decorate(observe_async) is observe_async


@pytest.mark.parametrize("keys", [(Ping, Ping), (Emit, Ping)])
async def test_multi_event_keys_create_independent_listeners(
    keys: tuple[type[Emit], type[Emit]],
) -> None:
    seen: list[Emit] = []

    class Recorder(PluginBase):
        @hook(*keys)
        def forward(self, event: Emit) -> None:
            seen.append(event)

    event = Ping()
    async with Scope() as scope:
        await scope.plugin(Recorder())
        seen.clear()
        await scope.dispatch(event)
    assert seen == [event, event]


@pytest.mark.parametrize("invalid", [None, "event", object, Ping()])
def test_multi_event_decorator_validates_every_key(invalid: object) -> None:
    with pytest.raises(TypeError, match="Hook keys must be Event classes"):
        hook(Ping, invalid, Pong)


@pytest.mark.parametrize("priority", [True, 1.5, "early"])
def test_multi_event_decorator_rejects_invalid_priorities(priority: object) -> None:
    with pytest.raises(TypeError, match="Listener priority must be an integer"):
        hook(Ping, Pong, priority=priority)


@pytest.mark.parametrize("keys", [(Ping, FirstWrap), (FirstWrap, Ping), (EmptyWrap, Ping)])
def test_multi_event_decorator_rejects_mixed_callback_shapes(
    keys: tuple[type[Emit | Waterfall], type[Emit | Waterfall]],
) -> None:
    with pytest.raises(TypeError, match="cannot mix observer and waterfall events"):
        hook(*keys)
