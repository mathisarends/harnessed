from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, overload, override

from pydantic import TypeAdapter, ValidationError

from harnessed.events import PluginValueChangedEvent
from harnessed.kernel import ServiceView
from harnessed.models.messages import freeze

if TYPE_CHECKING:
    from .log import SessionLog


class PluginValues:
    """Commit plugin values to the session log; the log's history owns the current values."""

    def __init__(self, log: SessionLog) -> None:
        self.log = log
        self.lock = asyncio.Lock()

    def raw(self, key: str) -> tuple[bool, Any]:
        values = self.log.plugin_values
        return key in values, values.get(key)

    async def commit(self, key: str, value: Any) -> None:
        """Append a change unless the committed value is already equal."""
        frozen = freeze(value)
        present, current = self.raw(key)
        if present and current == frozen:
            return
        await self.log.append(PluginValueChangedEvent(key, frozen))


def _value_adapter(schema: Any, *, nullable: bool) -> TypeAdapter[Any]:
    """Validate the declared schema, admitting None when the value has no default."""
    if nullable:
        return TypeAdapter(schema | None)
    return TypeAdapter(schema)


class SessionValue[T](ServiceView[PluginValues, "BoundSessionValue[T]"]):
    """Declare a typed plugin value that is persisted with the session under a stable key.

    The value survives compaction, plugin restarts, session snapshots and stores.
    Every instance of a plugin class shares the key, and child sessions start
    without values. Reads return the committed value or the default; writes are
    awaited because they append a ``plugin.value.changed`` event. Keep the key
    stable: renaming it orphans values in stored sessions.
    """

    @overload
    def __init__[V](self: SessionValue[V | None], schema: type[V], *, key: str) -> None: ...

    @overload
    def __init__[V](self: SessionValue[V], schema: type[V], *, key: str, default: V) -> None: ...

    def __init__(self, schema: Any, *, key: str, default: Any = None) -> None:
        super().__init__(PluginValues)
        if not isinstance(key, str) or not key:
            raise ValueError("SessionValue requires a non-empty key")
        self.key = key
        self.adapter: TypeAdapter[T] = _value_adapter(schema, nullable=default is None)
        self.default: T = default
        self.encode(default)

    def encode(self, value: T) -> Any:
        return self.adapter.dump_python(value, mode="json")

    def decode(self, raw: Any) -> T:
        try:
            return self.adapter.validate_python(raw)
        except ValidationError as error:
            raise ValueError(
                f"Stored session value {self.key!r} does not match its declared type"
            ) from error

    @override
    def present(self, service: PluginValues) -> BoundSessionValue[T]:
        return BoundSessionValue(service, self)


class BoundSessionValue[T]:
    """One activation's view of a declared SessionValue."""

    def __init__(self, store: PluginValues, declaration: SessionValue[T]) -> None:
        self._store = store
        self._declaration = declaration

    def get(self) -> T:
        """Return the committed value, or the declared default before the first write."""
        present, raw = self._store.raw(self._declaration.key)
        if not present:
            return self._declaration.default
        return self._declaration.decode(raw)

    async def set(self, value: T) -> None:
        """Commit a new value; equal values append nothing.

        Cancellation after the log accepted the change still leaves it committed,
        so read the value again instead of assuming the previous one.
        """
        async with self._store.lock:
            await self._store.commit(self._declaration.key, self._declaration.encode(value))

    async def update(self, change: Callable[[T], T]) -> T:
        """Apply a synchronous change to the current value and commit the result atomically."""
        async with self._store.lock:
            value = change(self.get())
            await self._store.commit(self._declaration.key, self._declaration.encode(value))
            return value
