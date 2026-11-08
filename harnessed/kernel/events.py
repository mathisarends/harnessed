from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Generic, Protocol, Self, TypeVar, cast, overload

from .di import BindingPlan, binding_plan
from .effects import Disposer, resolve
from .hooks import (
    HookFactory,
    ObserverCallback,
    ObserverFactory,
    ObserverHook,
    WaterfallCallback,
    WaterfallFactory,
    WaterfallHook,
)

_Result = TypeVar("_Result")

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .plugin import PluginHandle
    from .scope import Scope


class Event(Generic[_Result]):  # noqa: UP046 -- Invariance keeps the result contract fixed.
    @classmethod
    def _callback(cls, callback: Any) -> Any:
        return callback

    @classmethod
    def _hook(cls, owner=None, *, prepend=False, priority=0) -> Any:
        return ObserverHook(cls, owner, prepend, priority)


class Priority(IntEnum):
    """Lower priorities run first; equal priorities retain registration order."""

    GUARD = -100
    DEFAULT = 0
    OBSERVE = 100


class Emit(Event[None]):
    """Await observers by ascending priority, then registration order."""

    @classmethod
    def _callback(cls, callback: ObserverCallback[Self, None]) -> ObserverCallback[Self, None]:
        return callback

    @classmethod
    def _hook(cls, owner=None, *, prepend=False, priority=0) -> ObserverHook[Self, None]:
        return ObserverHook(cls, owner, prepend, priority)


@dataclass(frozen=True)
class PluginFailed(Emit):
    """A plugin setup failed; the original error is also reported by settle()."""

    plugin: str
    error: Exception


@dataclass(frozen=True)
class PluginActivated(Emit):
    """A retained recipe became active, including after dependency replacement."""

    handle: PluginHandle


class Bail[R](Event[R | None]):
    """Return the first result which is not None, including False and zero."""

    @classmethod
    def _callback(
        cls, callback: ObserverCallback[Self, R | None]
    ) -> ObserverCallback[Self, R | None]:
        return callback

    @classmethod
    def _hook(cls, owner=None, *, prepend=False, priority=0) -> ObserverHook[Self, R | None]:
        return ObserverHook(cls, owner, prepend, priority)


class Waterfall[R](Event[R]):
    """Middleware with a single-use continuation."""

    @classmethod
    def _callback(cls, callback: WaterfallCallback[Self, R]) -> WaterfallCallback[Self, R]:
        return callback

    @classmethod
    def _hook(cls, owner=None, *, prepend=False, priority=0) -> WaterfallHook[Self, R]:
        return WaterfallHook(cls, owner, prepend, priority)


class Parallel(Event[None]):
    """TaskGroup observers; failure cancels siblings."""

    @classmethod
    def _callback(cls, callback: ObserverCallback[Self, None]) -> ObserverCallback[Self, None]:
        return callback

    @classmethod
    def _hook(cls, owner=None, *, prepend=False, priority=0) -> ObserverHook[Self, None]:
        return ObserverHook(cls, owner, prepend, priority)


@dataclass(frozen=True)
class HookInvocation:
    """Identity of one invoked listener; event payloads are deliberately absent."""

    event: type[Event[Any]]
    registered_event: type[Event[Any]]
    callback: Callable[..., object]
    owner_name: str
    priority: int


class HookInstrumentation(Protocol):
    """Bracket an eligible callback without altering dispatch or its result."""

    def invoke(self, scope: Scope, invocation: HookInvocation) -> AbstractContextManager[None]: ...


@dataclass(eq=False)
class _Listener:
    event: type[Event[Any]]
    callback: Any
    owner: Scope
    plan: BindingPlan
    priority: int = 0
    active: bool = True


class EventBus:
    def __init__(self, parent: EventBus | None = None) -> None:
        self.parent = parent
        self.listeners: list[_Listener] = []

    def on(self, owner: Scope, event, callback, *, prepend=False, priority=0) -> Disposer:
        if not isinstance(event, type) or not issubclass(event, Event):
            raise TypeError("Listener keys must be Event classes")
        owner._check_open()
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise TypeError("Listener priority must be an integer")
        listener = _Listener(event, callback, owner, binding_plan(callback), priority)
        logger.debug(
            "Registered %s listener with owner %r at priority %d", event.__name__, owner, priority
        )
        if prepend:
            self.listeners.insert(0, listener)
        else:
            self.listeners.append(listener)

        def remove() -> None:
            listener.active = False
            self.listeners.remove(listener)

        return owner.effect(remove)

    def matching(self, event):
        inherited = self.parent.matching(event) if self.parent is not None else []
        return sorted(
            [
                *inherited,
                *(
                    listener
                    for listener in tuple(self.listeners)
                    if isinstance(event, listener.event) and listener.owner._registrations_active()
                ),
            ],
            key=lambda listener: listener.priority,
        )

    async def dispatch[R](
        self,
        scope: Scope,
        event: Event[R],
        *,
        default: Callable[[], R | Awaitable[R]] | None = None,
    ) -> R:
        listeners = self.matching(event)
        instrumentation = scope[HookInstrumentation] if HookInstrumentation in scope else None

        async def invoke(listener, *args):
            if not listener.active or not listener.owner._registrations_active():
                return None
            with listener.owner.activate():
                if instrumentation is None:
                    bound = listener.plan.bind(scope, args)
                    return await resolve(listener.callback(*bound.args, **bound.kwargs))
                invocation = HookInvocation(
                    type(event),
                    listener.event,
                    listener.callback,
                    listener.owner.owner_name,
                    listener.priority,
                )
                with instrumentation.invoke(scope, invocation):
                    bound = listener.plan.bind(scope, args)
                    return await resolve(listener.callback(*bound.args, **bound.kwargs))

        if isinstance(event, Waterfall):

            async def chain(index):
                while index < len(listeners) and (
                    not listeners[index].active
                    or not listeners[index].owner._registrations_active()
                ):
                    index += 1
                if index == len(listeners):
                    if default is None:
                        raise RuntimeError(f"No terminal handler for {type(event).__name__}")
                    return await resolve(default())
                called = False

                async def next_():
                    nonlocal called
                    if called:
                        raise RuntimeError("A waterfall continuation can only be called once")
                    called = True
                    return await chain(index + 1)

                return await invoke(listeners[index], event, next_)

            return cast(R, await chain(0))
        if isinstance(event, Parallel):
            async with asyncio.TaskGroup() as group:
                for listener in listeners:
                    group.create_task(invoke(listener, event))
            return cast(R, None)
        for listener in listeners:
            result = await invoke(listener, event)
            if isinstance(event, Bail) and result is not None:
                return cast(R, result)
        return cast(R, None)


@overload
def on[H](event: HookFactory[H], *, prepend: bool = False, priority: int = 0) -> H: ...


@overload
def on[E, R](
    event: ObserverFactory[E, R],
    other: ObserverFactory[E, R],
    *events: ObserverFactory[E, R],
    prepend: bool = False,
    priority: int = 0,
) -> ObserverHook[E, R]: ...


@overload
def on[E, R](
    event: WaterfallFactory[E, R],
    other: WaterfallFactory[E, R],
    *events: WaterfallFactory[E, R],
    prepend: bool = False,
    priority: int = 0,
) -> WaterfallHook[E, R]: ...


def on(
    event: object,
    *events: object,
    prepend: bool = False,
    priority: int = 0,
) -> object:
    """Declare a callback for each event, like stacked single-event decorators.

    Lower priorities run first; equal priorities retain registration order.
    Each key creates an independent listener, including repeated or overlapping keys.
    """
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise TypeError("Listener priority must be an integer")

    hooks: list[Callable[[Callable[..., object]], object]] = []
    for key in (event, *events):
        if not isinstance(key, type) or not issubclass(key, Event):
            raise TypeError("Hook keys must be Event classes")
        hooks.append(key._hook(prepend=prepend, priority=priority))
    if any(isinstance(declaration, WaterfallHook) for declaration in hooks) and not all(
        isinstance(declaration, WaterfallHook) for declaration in hooks
    ):
        raise TypeError("Shared hooks cannot mix observer and waterfall events")
    if not events:
        return hooks[0]

    def decorate[C: Callable[..., object]](callback: C) -> C:
        for declaration in hooks:
            declaration(callback)
        return callback

    return decorate
