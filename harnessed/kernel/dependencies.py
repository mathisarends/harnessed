from __future__ import annotations

from typing import TYPE_CHECKING, Self, overload

from .di import Dependency
from .keys import Key, ServiceKey

if TYPE_CHECKING:
    from .declarations import PluginBase


class DependencyField[T]:
    """A stable, read-only service binding owned by one plugin activation."""

    def __init__(self, key: ServiceKey[T], *, optional: bool = False) -> None:
        if not isinstance(key, type | Key):
            raise TypeError("Service bindings require a service class, Protocol or named Key")
        self.binding = Dependency(key, optional)
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> Self: ...

    @overload
    def __get__(self, instance: PluginBase, owner: type | None = None) -> T: ...

    def __get__(self, instance: PluginBase | None, owner: type | None = None) -> T | Self:
        if instance is None:
            return self
        if instance._mount_injections is None:
            raise RuntimeError(
                f"Dependency {type(instance).__name__}.{self._name} is available only on a "
                "mounted plugin, not its recipe (or a disposed activation)"
            )
        return instance._mount_injections[self._name]

    def __set__(self, instance: PluginBase, value: T) -> None:
        raise AttributeError("Dependency bindings are read-only for the entire activation")


def needs[T](key: ServiceKey[T]) -> DependencyField[T]:
    """Require a service for activation and bind it before setup.

    The binding stays stable through cleanup. Provider loss ends the activation;
    replacement or restoration creates a fresh instance and state.
    """
    return DependencyField(key)


def observes[T](key: ServiceKey[T]) -> DependencyField[T | None]:
    """Bind a service or None without making its presence an activation requirement.

    Appearance, disappearance and replacement create a fresh activation and state.
    Mutations within the same service object do not restart the plugin.
    """
    return DependencyField(key, optional=True)
