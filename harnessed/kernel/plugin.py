from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, suppress
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .declarations import Bundle, PluginBase
from .di import bind_injected
from .events import PluginActivated, PluginFailed
from .keys import MissingService, ServiceKey
from .spec import PluginDependencyIssue, PluginSpec, awaits_provider

if TYPE_CHECKING:
    from .scope import Scope

logger = logging.getLogger(__name__)


class PluginStatus(StrEnum):
    WAITING = "waiting"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"
    FAILED = "failed"
    DISPOSED = "disposed"


class PluginHandle:
    """An owned recipe. Dependency loss suspends it; aclose permanently removes it."""

    def __init__(self, spec: PluginSpec, mount: Scope) -> None:
        self.name = spec.name
        self.spec = spec
        self._mount = mount
        self._runtime = mount._runtime
        self.scope = mount._new_owner()
        self.scope._closed = True
        self.status: PluginStatus = PluginStatus.WAITING
        self.error: Exception | None = None
        self._disposed = False
        self._transition = asyncio.Lock()
        self._active_signature = None
        self._failed_signature = None
        self._starting_signature = None
        self._start_task: asyncio.Task | None = None
        self._dependents: set[PluginHandle] = set()
        self._instance: PluginBase | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    @property
    def instance(self) -> PluginBase | None:
        """The current class-plugin activation, or None while waiting or disposed."""
        return self._instance

    @property
    def missing_services(self) -> tuple[ServiceKey[Any], ...]:
        """Required keys unavailable under the same visibility rules as activation."""
        missing: list[ServiceKey[Any]] = []
        for key in self.spec.needs:
            try:
                binding = self._mount._binding(key, exclude=self)
            except MissingService:
                missing.append(key)
                continue
            provider = binding.owner._handle
            if provider is not None and provider.status != PluginStatus.ACTIVE:
                missing.append(key)
        return tuple(missing)

    async def aclose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._runtime.remove(self)
        try:
            await self._suspend()
        finally:
            self.status = PluginStatus.DISPOSED
            self._runtime.changed()

    def _signature(self):
        try:
            bindings = tuple(self._mount._binding(key, exclude=self) for key in self.spec.needs)
        except MissingService:
            return None
        if any(
            binding.owner._handle is not None
            and binding.owner._handle.status != PluginStatus.ACTIVE
            for binding in bindings
        ):
            return None
        observed = []
        for key in self.spec.observes:
            try:
                observed.append(self._mount._binding(key, exclude=self))
            except MissingService:
                observed.append(None)
        return (*bindings, *observed)

    def _cycle(self, signature):
        def visit(handle, path):
            if handle is self:
                return [self, *path, self]
            if handle in path:
                return None
            for binding in handle._active_signature or ():
                if binding is None:
                    continue
                provider = binding.owner._handle
                if provider is not None and (cycle := visit(provider, [*path, handle])):
                    return cycle
            return None

        for binding in signature or ():
            if binding is None:
                continue
            provider = binding.owner._handle
            if provider is not None and (cycle := visit(provider, [])):
                return cycle
        return None

    async def _activate(self) -> None:
        if self._disposed or self._mount._closed or self.status == PluginStatus.ACTIVE:
            return
        if self._start_task is None or self._start_task.done():
            self._starting_signature = self._signature()
            self.status = PluginStatus.STARTING
            self._start_task = asyncio.create_task(
                self._activate_worker(), name=f"harnessed:setup:{self.name}"
            )
        try:
            await asyncio.shield(self._start_task)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                self._start_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._start_task
                raise
            if not (
                self._disposed
                or self._mount._closed
                or self._signature() != self._starting_signature
            ):
                raise

    async def _activate_worker(self) -> None:
        async with self._transition:
            signature = self._signature()
            if (
                self._disposed
                or self._mount._closed
                or signature is None
                or self.status == PluginStatus.ACTIVE
            ):
                self.status = PluginStatus.WAITING
                return
            self.status = PluginStatus.STARTING
            child = self._mount._new_owner()
            self.scope = child
            child._handle = self
            self._depend_on_providers(child, signature)
            try:
                name = self.spec.name
                generator = await self._start(child)

                async def teardown() -> None:
                    with child.activate():
                        try:
                            await anext(generator)
                        except StopAsyncIteration:
                            return
                        finally:
                            await generator.aclose()
                    raise RuntimeError(f"Plugin {name} yielded more than once")

                child.effect(teardown)
                self._active_signature = signature
                self._failed_signature = None
                self.error = None
                self.status = PluginStatus.ACTIVE
                await self._mount.dispatch(PluginActivated(self))
                self._runtime.changed()
            except BaseException as exc:
                await self._activation_failed(exc, signature)
                try:
                    await child.aclose()
                finally:
                    self._instance = None
                raise

    def _depend_on_providers(self, child: Scope, signature) -> None:
        for binding in signature:
            if binding is None:
                continue
            provider = binding.owner._handle
            if provider is not None:
                provider._dependents.add(self)
                child.effect(lambda p=provider: p._dependents.discard(self))

    async def _start(self, child: Scope) -> AsyncGenerator[Any]:
        """Run the plugin factory in its activation scope up to its single yield."""
        spec = self.spec
        with child.activate():
            bound = bind_injected(spec.factory, child, (child, *spec.args), kwargs=spec.kwargs)
            generator = spec.factory(*bound.args, **bound.kwargs)
            if not inspect.isasyncgen(generator):
                raise TypeError(f"Plugin {spec.name} must be an async generator yielding once")
            try:
                instance = await anext(generator)
                self._instance = instance if isinstance(instance, PluginBase) else None
            except StopAsyncIteration as exc:
                raise RuntimeError(f"Plugin {spec.name} did not yield") from exc
        return generator

    async def _activation_failed(self, exc: BaseException, signature) -> None:
        cancelled = isinstance(exc, asyncio.CancelledError)
        self.status = PluginStatus.WAITING if cancelled else PluginStatus.FAILED
        self._failed_signature = None if cancelled else signature
        self.error = exc if isinstance(exc, Exception) else None
        if self.error is not None:
            logger.error("Plugin %s setup failed", self.name, exc_info=exc)
            try:
                await self._mount.dispatch(PluginFailed(self.name, self.error))
            except Exception:
                # Dispose the activation even when a user observer fails.
                logger.exception("PluginFailed observer failed for %s", self.name)

    async def _suspend(self, visited: set | None = None) -> None:
        if self.status == PluginStatus.STARTING and self._start_task is not None:
            self._start_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._start_task
        visited = set() if visited is None else visited
        if self in visited:
            return
        visited.add(self)
        async with self._transition:
            if self.status not in (PluginStatus.ACTIVE, PluginStatus.FAILED):
                return
            self.status = PluginStatus.STOPPING
            try:
                async with AsyncExitStack() as stack:
                    stack.push_async_callback(self.scope.aclose)
                    for dependent in tuple(self._dependents):
                        stack.push_async_callback(dependent._suspend, visited)
            finally:
                self.status = PluginStatus.WAITING
                self._active_signature = None
                self._instance = None
                self._runtime.changed()


class PluginRuntime:
    def __init__(self) -> None:
        self._handles: list[PluginHandle] = []
        self.task: asyncio.Task | None = None
        self.dirty = False
        self.errors: list[Exception] = []

    def retain(self, spec: PluginSpec, mount: Scope) -> PluginHandle:
        handle = PluginHandle(spec, mount)
        self._handles.append(handle)
        return handle

    def remove(self, handle: PluginHandle) -> None:
        self._handles.remove(handle)

    def diagnostics(self) -> tuple[PluginDependencyIssue, ...]:
        return tuple(
            PluginDependencyIssue(handle.spec, handle.missing_services)
            for handle in self._handles
            if handle.status == PluginStatus.WAITING and not handle._mount._closed
        )

    def changed(self) -> None:
        self.dirty = True
        for handle in tuple(self._handles):
            if (
                handle.status == PluginStatus.STARTING
                and handle._start_task is not None
                and (handle._mount._closed or handle._signature() != handle._starting_signature)
            ):
                handle._start_task.cancel()
        if not self._handles or (self.task is not None and not self.task.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.task = loop.create_task(self._reconcile(), name="harnessed:composition")

    async def _reconcile(self) -> None:
        blocked = set()
        while self.dirty:
            self.dirty = False
            deferred: PluginHandle | None = None
            for handle in tuple(self._handles):
                if handle in blocked or (
                    handle._disposed
                    or handle._mount._closed
                    or handle.status in (PluginStatus.STARTING, PluginStatus.STOPPING)
                ):
                    continue
                try:
                    if await self._reconcile_handle(handle, blocked) and deferred is None:
                        deferred = handle
                except Exception as exc:
                    # Collect arbitrary failures so other plugins can reconcile.
                    self.errors.append(exc)
            if deferred is not None and not self.dirty:
                # No provider made progress, so the observer stops waiting for one.
                try:
                    await deferred._activate()
                except Exception as exc:
                    # Collect arbitrary failures so other plugins can reconcile.
                    self.errors.append(exc)

    async def _reconcile_handle(self, handle: PluginHandle, blocked: set[PluginHandle]) -> bool:
        """Bring one handle up to date; True when it waits for an observed provider."""
        signature = handle._signature()
        cycle = handle._cycle(signature)
        if cycle:
            blocked.update(cycle)
            await handle._suspend()
            raise RuntimeError("Cyclic plugin needs: " + " -> ".join(h.name for h in cycle))
        if handle.status == PluginStatus.ACTIVE and signature != handle._active_signature:
            await handle._suspend()
        signature = handle._signature()
        if (
            signature is None
            or handle.status not in (PluginStatus.WAITING, PluginStatus.FAILED)
            or signature == handle._failed_signature
        ):
            return False
        if self._awaits_provider(handle):
            return True
        await handle._activate()
        return False

    def _awaits_provider(self, handle: PluginHandle) -> bool:
        """Whether a provider of a service this handle observes is still about to start."""
        mount = handle._mount
        waiting = [
            other.spec
            for other in self._handles
            if other._mount is mount
            and other.status in (PluginStatus.WAITING, PluginStatus.STARTING)
        ]
        return awaits_provider(handle.spec, waiting, mount.__contains__)


type PluginLike = PluginSpec | Bundle | PluginBase


def as_plugin_spec(value: PluginLike) -> PluginSpec:
    if isinstance(value, PluginSpec):
        return value
    if isinstance(value, Bundle | PluginBase):
        return value._as_plugin_spec()
    raise TypeError(f"Expected a configured plugin, Bundle or Plugin, got {type(value).__name__}")
