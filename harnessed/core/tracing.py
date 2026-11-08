from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from harnessed.core.plugin import Plugin
from harnessed.kernel import HookInvocation, PluginScope, Scope


class Span(Protocol):
    """Runtime span attributes may include typed request, usage and result values.

    Exporters decide whether to capture content; the default tracer discards it
    without serialization. Scalar attributes describe IDs, outcomes and timing.
    """

    def set_attribute(self, name: str, value: Any) -> None: ...


class Tracer(Protocol):
    """Open spans using the supplied name verbatim, without a vendor dependency."""

    def span(self, name: str, **attributes: Any) -> AbstractContextManager[Span]: ...


@dataclass(frozen=True)
class TraceSession:
    """Live session correlation, independent of conversation storage and exporters."""

    session_id: str


def current() -> Tracer:
    """Return the execution context's tracer, or a no-op outside traced work."""
    return _current.get()


@contextmanager
def span(
    name: str, *, scope: Scope | PluginScope | None = None, **attributes: Any
) -> Generator[Span]:
    """Open a span across synchronous or asynchronous work.

    An explicit owner takes precedence over ambient context, including when it
    has no tracer. The chosen tracer stays fixed until the span exits.
    """
    tracer = _select(scope) if scope is not None else current()
    session_id = _session(scope) if scope is not None else _session_id.get()
    if session_id is not None:
        attributes.setdefault("session_id", session_id)
    with _using(tracer, session_id), tracer.span(name, **attributes) as active:
        yield active


@contextmanager
def bind_tracer(scope: Scope | PluginScope) -> Generator[None]:
    """Bind an owner's tracer without opening a span or changing its lifetime."""
    with _using(_select(scope), _session(scope)):
        yield


class HookTracing:
    """Bridge optional kernel invocation instrumentation to the selected tracer."""

    def __init__(self, *, trace_hooks: bool = False) -> None:
        self.trace_hooks = trace_hooks

    @contextmanager
    def invoke(self, scope: Scope, invocation: HookInvocation) -> Generator[None]:
        with bind_tracer(scope):
            if not self.trace_hooks:
                yield
                return
            callback = invocation.callback
            # Callable objects need not expose function metadata. Reflection here
            # describes a heterogeneous hook registry, never application content.
            name = getattr(callback, "__qualname__", type(callback).__qualname__)
            module = getattr(callback, "__module__", type(callback).__module__)
            event = invocation.event
            registered = invocation.registered_event
            registered_name = f"{registered.__module__}.{registered.__qualname__}"
            with span(
                "harnessed.hook",
                **{
                    "harnessed.hook.event": f"{event.__module__}.{event.__qualname__}",
                    "harnessed.hook.registered_event": registered_name,
                    "code.function.name": f"{module}.{name}",
                    "harnessed.hook.owner": invocation.owner_name,
                    "harnessed.hook.priority": invocation.priority,
                },
            ):
                yield


class NoopTracer(Plugin, slot="tracer"):
    """Default tracing implementation with no exporter or third-party imports."""

    def span(self, name: str, **attributes: Any) -> AbstractContextManager[Span]:
        return nullcontext(_NoopSpan())

    async def setup(self, scope: PluginScope) -> None:
        scope.provide(Tracer, self)


class _NoopSpan:
    def set_attribute(self, name: str, value: Any) -> None:
        pass


class _NoopTracer:
    def span(self, name: str, **attributes: Any) -> AbstractContextManager[Span]:
        return nullcontext(_NoopSpan())


def _select(scope: Scope | PluginScope) -> Tracer:
    return scope[Tracer] if Tracer in scope else _NOOP


def _session(scope: Scope | PluginScope) -> str | None:
    return scope[TraceSession].session_id if TraceSession in scope else None


@contextmanager
def _using(tracer: Tracer, session_id: str | None) -> Generator[None]:
    token = _current.set(tracer)
    correlation = _session_id.set(session_id)
    try:
        yield
    finally:
        _session_id.reset(correlation)
        _current.reset(token)


_NOOP = _NoopTracer()
_current: ContextVar[Tracer] = ContextVar("harnessed_tracer", default=_NOOP)
_session_id: ContextVar[str | None] = ContextVar("harnessed_trace_session", default=None)
