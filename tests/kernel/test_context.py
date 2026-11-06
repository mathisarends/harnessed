import asyncio
from dataclasses import dataclass

import pytest

from harnessed.kernel import (
    Bail,
    Emit,
    Inject,
    Parallel,
    PluginStatus,
    Scope,
    ScopeClosed,
    Waterfall,
    plugin,
)


class Service:
    pass


@dataclass(frozen=True)
class Ping(Emit):
    value: int = 1


class Choose(Bail[bool]):
    pass


class Wrap(Waterfall[str]):
    pass


class Together(Parallel):
    pass


async def test_dispatch_order_bail_and_waterfall():
    async with Scope() as scope:
        seen = []
        scope.on(Ping, lambda event: seen.append(2))
        scope.on(Ping, lambda event: seen.append(1), prepend=True)
        assert await scope.dispatch(Ping()) is None
        assert seen == [1, 2]
        scope.on(Choose, lambda event: None)
        scope.on(Choose, lambda event: False)
        scope.on(Choose, lambda event: pytest.fail("Bail must stop at False"))
        assert await scope.dispatch(Choose()) is False

        @scope.on(Wrap)
        async def around(event, next_):
            return "[" + await next_() + "]"

        assert await scope.dispatch(Wrap(), default=lambda: "core") == "[core]"


async def test_waterfall_continuation_is_single_use():
    async with Scope() as scope:

        @scope.on(Wrap)
        async def bad(event, next_):
            await next_()
            return await next_()

        with pytest.raises(RuntimeError, match="only be called once"):
            await scope.dispatch(Wrap(), default=lambda: "core")


async def test_parallel_observers_overlap_and_failure_cancels_siblings():
    async with Scope() as scope:
        started, release, canceled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        @scope.on(Together)
        async def first(event):
            started.set()
            try:
                await release.wait()
            finally:
                canceled.set()

        @scope.on(Together)
        async def second(event):
            await started.wait()
            raise ValueError("observer failed")

        with pytest.raises(ExceptionGroup):
            await scope.dispatch(Together())
        assert canceled.is_set()


async def test_setup_failure_rolls_back_all_effects():
    cleaned = []

    @plugin
    async def broken(scope):
        scope.provide(Service, Service())
        scope.on(Ping, lambda event: pytest.fail("Failed plugin left an active listener"))
        scope.effect(lambda: cleaned.append("cleanup"))
        raise ValueError("setup failed")
        yield

    async with Scope() as scope:
        with pytest.raises(ValueError, match="setup failed"):
            await scope.plugin(broken())
        assert Service not in scope
        assert await scope.dispatch(Ping()) is None
    assert cleaned == ["cleanup"]


async def test_explicit_dependencies_and_provider_unload():
    teardown = []

    @plugin
    async def provider(scope):
        scope.provide(Service, Service())
        yield
        teardown.append("provider")

    @plugin(name="consumer", needs=(Service,))
    async def consumer(scope, service: Inject[Service]):
        assert service is scope[Service]
        yield
        teardown.append("consumer")

    async with Scope() as scope:
        handles = await scope.install([consumer(), provider()])
        assert [h.name for h in handles] == ["provider", "consumer"]
        await handles[0].aclose()
        assert teardown == ["consumer", "provider"]
        assert Service not in scope
        with pytest.raises(ScopeClosed):
            handles[1].scope.provide(Service, Service())
    assert teardown == ["consumer", "provider"]


async def test_install_missing_dependencies_rolls_back_mounted_plugins():
    @plugin
    async def available(scope):
        scope.provide(int, 1)
        yield

    @plugin(needs=[Service])
    async def missing(scope):
        yield

    async with Scope() as scope:
        with pytest.raises(RuntimeError, match="missing or cyclic"):
            await scope.install([available(), missing()], strict=True)
        assert int not in scope


async def test_context_children_shadow_and_restore_without_sibling_leaks():
    async with Scope() as root:
        root.provide(int, 1)
        child, sibling = root.extend(), root.extend()
        remove = child.provide(int, 2)
        assert (root[int], child[int], sibling[int]) == (1, 2, 1)
        child.on(Ping, lambda event: pytest.fail("Child listener leaked"))
        await sibling.dispatch(Ping())
        await remove()
        await remove()
        assert child[int] == 1


async def test_listener_injection_is_resolved_at_each_dispatch():
    async with Scope() as scope:
        scope.provide(int, 1)
        observed = []

        @scope.on(Ping)
        def observe(event, number: Inject[int]):
            observed.append(number)

        await scope.dispatch(Ping())
        restore = scope.provide(int, 2)
        await scope.dispatch(Ping())
        await restore()
        await scope.dispatch(Ping())
        assert observed == [1, 2, 1]


async def test_bad_plugin_yields_and_teardown_failure_still_clean_registrations():
    @plugin()
    async def twice(scope):
        scope.provide(int, 7)
        yield
        yield

    async with Scope() as scope:
        handle = await scope.plugin(twice())
        with pytest.raises(RuntimeError, match="more than once"):
            await handle.aclose()
        assert int not in scope


async def test_all_cleanup_callbacks_run_when_one_raises():
    scope = Scope()
    cleaned = []
    scope.effect(lambda: cleaned.append("first"))

    def fail():
        raise ValueError("cleanup")

    scope.effect(fail)
    scope.effect(lambda: cleaned.append("last"))
    with pytest.raises(ValueError):
        await scope.aclose()
    assert cleaned == ["last", "first"]


async def test_base_and_specific_listeners_keep_registration_order():
    async with Scope() as scope:
        seen = []
        scope.on(Ping, lambda event: seen.append(1))
        scope.on(Emit, lambda event: seen.append(2))
        scope.on(Ping, lambda event: seen.append(3))
        await scope.dispatch(Ping())
        assert seen == [1, 2, 3]


async def test_provider_unload_removes_services_even_if_consumer_teardown_fails():
    @plugin
    async def provider(scope):
        scope.provide(Service, Service())
        yield

    @plugin(needs=(Service,))
    async def consumer(scope, service: Inject[Service]):
        yield
        raise ValueError("consumer teardown")

    async with Scope() as scope:
        provider_handle, _ = await scope.install([provider(), consumer()])
        with pytest.raises(ValueError, match="consumer teardown"):
            await provider_handle.aclose()
        assert Service not in scope


async def test_listener_disposed_during_dispatch_does_not_run_from_stale_snapshot():
    @plugin
    async def observer(scope):
        scope.on(Ping, lambda event: pytest.fail("Disposed listener was called"))
        yield

    async with Scope() as scope:
        handle = None

        @scope.on(Ping)
        async def unload(event):
            await handle.aclose()

        handle = await scope.plugin(observer())
        await scope.dispatch(Ping())
        assert handle.status == PluginStatus.DISPOSED


async def test_manually_disposed_listener_is_skipped_in_an_existing_dispatch_snapshot():
    async with Scope() as scope:
        remove = None
        seen = []

        async def first(event):
            seen.append(event)
            await remove()

        scope.add_listener(Ping, first)
        remove = scope.add_listener(Ping, lambda event: pytest.fail("Removed listener ran"))
        event = Ping()
        await scope.dispatch(event)
        assert seen == [event]
