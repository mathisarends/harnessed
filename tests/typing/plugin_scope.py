from contextlib import AbstractAsyncContextManager
from typing import assert_type, override

from harnessed import Plugin
from harnessed.kernel import Disposer, Emit, Next, PluginScope, Scope, Waterfall


class Changed(Emit):
    pass


class Transform(Waterfall[str]):
    pass


class Feature(Plugin):
    @override
    async def setup(self, scope: PluginScope) -> None:
        assert_type(scope.owner_name, str)

    @override
    async def teardown(self, scope: PluginScope) -> None:
        assert_type(await scope.dispatch(Changed()), None)


async def operations(scope: PluginScope, resource: AbstractAsyncContextManager[str]) -> None:
    assert_type(await scope.enter(resource), str)
    assert_type(scope.provide(str, "value"), Disposer)
    assert_type(scope[str], str)
    assert_type(str in scope, bool)
    assert_type(scope.effect(lambda: None), Disposer)
    assert_type(scope.register(lambda registration: lambda: None), Disposer)
    assert_type(scope.on(Changed, lambda event: None), Disposer)

    @scope.on(Transform)
    async def transform(event: Transform, next_: Next[str]) -> str:
        return await next_()

    assert_type(await scope.dispatch(Transform(), default=lambda: "value"), str)
    scope.on(Changed, transform)  # ty: ignore[no-matching-overload]
    _ = scope.install  # ty: ignore[unresolved-attribute]
    _ = scope.plugin  # ty: ignore[unresolved-attribute]
    _ = scope.settle  # ty: ignore[unresolved-attribute]
    _ = scope.extend  # ty: ignore[unresolved-attribute]
    _ = scope.aclose  # ty: ignore[unresolved-attribute]
    _ = scope.activate  # ty: ignore[unresolved-attribute]


class FullScopeOverride(Plugin):
    @override
    async def setup(self, scope: Scope) -> None:  # ty: ignore[invalid-method-override]
        pass
