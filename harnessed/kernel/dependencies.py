from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Self, overload

from .di import Dependency
from .keys import Key, ServiceKey

if TYPE_CHECKING:
    from .declarations import PluginBase


class DependencyField[T]:
    """A stable, read-only service binding owned by one plugin activation."""

    def __init__(self, key: ServiceKey[T], *, optional: bool = False) -> None:
        if not isinstance(key, type | Key):
            raise TypeError("dependency requires a service class, Protocol or named Key")
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


@overload
def dependency[T](
    key: ServiceKey[T], *, optional: Literal[False] = False
) -> DependencyField[T]: ...


@overload
def dependency[T](key: ServiceKey[T], *, optional: Literal[True]) -> DependencyField[T | None]: ...


def dependency[T](
    key: ServiceKey[T], *, optional: bool = False
) -> DependencyField[T] | DependencyField[T | None]:
    """Declare a required binding, or a nonblocking observation with optional=True.

    Both restart the activation when the visible provider changes. Ordinary type
    annotations and call-scoped Inject parameters never add lifetime requirements.
    """
    return DependencyField(key, optional=optional)
