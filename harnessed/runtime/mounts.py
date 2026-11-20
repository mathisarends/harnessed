from collections.abc import Generator
from typing import Any

from harnessed.events.registry import EventRegistry
from harnessed.kernel import PluginHandle, Scope
from harnessed.prompts import SectionDelivery
from harnessed.prompts.delivery import runtime_sections
from harnessed.tools import ToolRegistry

from .plugin import Plugin, plugin_contribution, plugin_events


class PluginMount:
    """A single activation request. Context exit releases the handle it acquired."""

    def __init__(
        self,
        scope: Scope,
        registry: EventRegistry,
        plugin: Plugin,
        *,
        section_delivery: SectionDelivery,
    ) -> None:
        if not isinstance(plugin, Plugin):
            raise TypeError("Expected a Plugin instance")
        self._scope = scope
        self._registry = registry
        self._plugin = plugin
        self._delivery = SectionDelivery(section_delivery)
        self._started = False
        self._owned: PluginHandle | None = None

    def __await__(self) -> Generator[Any, None, PluginHandle]:
        return self._open().__await__()

    async def __aenter__(self) -> PluginHandle:
        handle = await self._open()
        self._owned = handle
        return handle

    async def __aexit__(self, *_exc: object) -> None:
        if self._owned is not None:
            await self._owned.aclose()

    async def _open(self) -> PluginHandle:
        if self._started:
            raise RuntimeError("A mount request can only be opened once")
        self._started = True
        contribution = plugin_contribution(self._plugin)
        spec = runtime_sections(self._plugin, contribution, self._delivery)
        self._registry.register(plugin_events(self._plugin))
        handle = await self._scope.plugin(spec)
        try:
            await self._scope.settle()
            self._scope[ToolRegistry].validate_references()
        except BaseException:
            # Roll back the mount on failed activation or caller cancellation.
            await handle.aclose()
            raise
        return handle
