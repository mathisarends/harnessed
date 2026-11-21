from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import ExitStack
from json import dumps
from typing import TYPE_CHECKING, Any, overload, override

from pydantic import TypeAdapter, ValidationError

from harnessed.events import PluginValueChangedEvent
from harnessed.kernel import ServiceView
from harnessed.models.messages import JSONValue, freeze, to_dict

if TYPE_CHECKING:
    from .log import SessionLog


class PluginValues:
    """Commit plugin values to the session log; the log's history owns the current values."""

    def __init__(self, log: SessionLog) -> None:
        self._log = log
        self._lock = asyncio.Lock()

    def raw(self, key: str) -> tuple[bool, JSONValue]:
        values = self._log.plugin_values
        return key in values, values.get(key)

    async def commit(
        self, key: str, value: JSONValue, *, on_committed: Callable[[], None] | None = None
    ) -> None:
        """Append an encoded change unless the committed value is already equal."""
        event = PluginValueChangedEvent(key, value)
        present, current = self.raw(key)
        if not present or current != event.value:
            await self._log.append(event, on_committed=on_committed)

    async def change[T](self, declaration: SessionValue[T], candidate: Callable[[], T]) -> T:
        """Serialize changes through commit, then allow observers to write values too."""
        with ExitStack() as guard:
            await self._lock.acquire()
            guard.callback(self._lock.release)
            raw = declaration.encode(candidate())
            value = declaration.decode(raw)
            await self.commit(declaration.key, raw, on_committed=guard.close)
            return value


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
        self._adapter: TypeAdapter[T] = _value_adapter(schema, nullable=default is None)
        self._default = self.encode(default)
        _ = self.decode(self._default)

    @property
    def default(self) -> T:
        """Return a fresh value from the declaration's default snapshot."""
        return self.decode(self._default)

    def encode(self, value: T) -> JSONValue:
        raw = self._adapter.dump_python(value, mode="json", round_trip=True, warnings="error")
        return freeze(raw)

    def decode(self, raw: JSONValue) -> T:
        payload = dumps(to_dict(raw), allow_nan=False)
        try:
            return self._adapter.validate_json(payload)
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
        await self._store.change(self._declaration, lambda: value)

    async def update(self, change: Callable[[T], T]) -> T:
        """Apply a synchronous change to the current value and commit the result atomically."""
        return await self._store.change(self._declaration, lambda: change(self.get()))
