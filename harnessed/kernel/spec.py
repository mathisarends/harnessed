from collections.abc import AsyncGenerator, Callable, Mapping
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
