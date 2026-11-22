from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .keys import ServiceKey


@dataclass(frozen=True)
class PluginSpec:
    __hash__ = None
    name: str
    factory: Callable[..., AsyncGenerator[object, None]]
    needs: tuple[ServiceKey[Any], ...]
    args: tuple[Any, ...]
    kwargs: Mapping[str, Any]
    slot: str | None = None
    provides: tuple[ServiceKey[Any], ...] = ()
    observes: tuple[ServiceKey[Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kwargs", MappingProxyType(dict(self.kwargs)))
        object.__setattr__(self, "needs", tuple(dict.fromkeys(self.needs)))
        object.__setattr__(
            self,
            "observes",
            tuple(key for key in dict.fromkeys(self.observes) if key not in self.needs),
        )


@dataclass(frozen=True)
class PluginDependencyIssue:
    """A waiting recipe and the service identities unavailable to its mount."""

    plugin: PluginSpec
    missing: tuple[ServiceKey[Any], ...]


def awaits_provider(
    spec: PluginSpec,
    waiting: Sequence[PluginSpec],
    available: Callable[[ServiceKey[Any]], bool] | None,
) -> bool:
    """Whether a waiting recipe declares a service ``spec`` observes and can start first.

    Mounting that provider first spares the observer a restart. A provider that
    can only start after ``spec`` does not count, so ``observes`` never blocks.
    """
    providers = (
        other
        for other in waiting
        if other is not spec and any(key in other.provides for key in spec.observes)
    )
    if available is None:
        # During staged installation, another phase can supply missing services.
        return any(providers)
    return any(_can_start(provider, waiting, available, path=(spec,)) for provider in providers)


def _can_start(
    spec: PluginSpec,
    waiting: Sequence[PluginSpec],
    available: Callable[[ServiceKey[Any]], bool],
    *,
    path: tuple[PluginSpec, ...],
) -> bool:
    if any(spec is visited for visited in path):
        return False
    visited = (*path, spec)
    return all(
        available(key)
        or any(
            key in other.provides and _can_start(other, waiting, available, path=visited)
            for other in waiting
        )
        for key in spec.needs
    )
