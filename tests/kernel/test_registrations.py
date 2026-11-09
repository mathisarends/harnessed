import inspect
from collections.abc import Callable
from functools import wraps

import pytest

from harnessed.kernel import (
    Emit,
    PluginBase,
    PluginStatus,
    Scope,
    as_plugin_spec,
    hook,
    registration,
)


class Ping(Emit):
    pass


class Listener:
    needs = (str,)

    def __init__(self) -> None:
        self.callbacks: list[Callable[..., object]] = []
        self.cleaned: list[Callable[..., object]] = []

    def mount(self, scope: Scope, callback: Callable[..., object]) -> None:
        self.callbacks.append(callback)
        scope.on(Ping, callback)
        scope.effect(lambda: self.cleaned.append(callback))


def test_declaration_preserves_identity_and_signature() -> None:
    def callback(value: int, *, text: str = "") -> str:
        return text * value

    original = inspect.signature(callback)
    assert registration(Listener())(callback) is callback
    assert inspect.signature(callback) == original
    assert callback(2, text="a") == "aa"


async def test_registrations_bind_fresh_instances_and_collect_dependencies() -> None:
    listener = Listener()
    calls: list[PluginBase] = []

    class Feature(PluginBase, provides=True):
        @registration(listener)
        def receive(self, event: Ping) -> None:
            calls.append(self)

    recipe = Feature()
    assert as_plugin_spec(recipe).needs == (str,)
    async with Scope() as first, Scope() as second:
        first.provide(str, "first")
        second.provide(str, "second")
        handle = await first.plugin(recipe)
        await second.plugin(recipe)
        owner = first[Feature]
        assert listener.callbacks == [owner.receive, second[Feature].receive]
        assert owner is not recipe and owner is not second[Feature]
        with second.activate():
            owner.receive(Ping())
        await first.dispatch(Ping())
        assert calls == [owner, owner]
        await handle.aclose()
        await first.dispatch(Ping())
        assert calls == [owner, owner]
        assert listener.cleaned == [owner.receive]
    assert listener.cleaned == listener.callbacks


async def test_wrapped_declarations_do_not_mutate_the_original_registrations() -> None:
    first = Listener()
    second = Listener()
    calls: list[str] = []

    class Original(PluginBase, provides=True):
        @registration(first)
        def receive(self, event: Ping) -> None:
            calls.append("original")

    class Wrapped(Original, provides=True):
        @registration(second)
        @wraps(Original.receive)
        def receive(self, event: Ping) -> None:
            calls.append("wrapped")

    async with Scope() as scope:
        scope.provide(str, "ready")
        await scope.plugin(Original())
        await scope.plugin(Wrapped())
        await scope.dispatch(Ping())
        assert calls == ["original", "wrapped", "wrapped"]
        assert first.callbacks == [scope[Original].receive, scope[Wrapped].receive]
        assert second.callbacks == [scope[Wrapped].receive]


@pytest.mark.parametrize("fail", [False, True])
async def test_hook_composition_and_failed_activation_cleanup(fail: bool) -> None:
    listener = Listener()
    calls: list[str] = []

    class Broken:
        needs = ()

        def mount(self, scope: Scope, callback: Callable[..., object]) -> None:
            if fail:
                raise ValueError("registration failed")

    class Feature(PluginBase):
        @registration(Broken())
        @registration(listener)
        @hook(Ping)
        def receive(self, event: Ping) -> None:
            calls.append("ping")

    async with Scope() as scope:
        scope.provide(str, "ready")
        if fail:
            with pytest.raises(ValueError, match="registration failed"):
                await scope.plugin(Feature())
        else:
            handle = await scope.plugin(Feature())
            assert handle.status is PluginStatus.ACTIVE
        await scope.dispatch(Ping())
        assert calls == ([] if fail else ["ping", "ping"])
    assert listener.cleaned == listener.callbacks
