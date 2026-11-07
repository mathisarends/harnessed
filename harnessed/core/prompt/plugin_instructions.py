import asyncio

from harnessed.core.events.messages import PluginInstructionsChangedEvent
from harnessed.core.session.session import SessionLog


class PluginInstructions:
    """Persist feature-owned instruction state independently of conversation text.

    Features render their own values through PrepareRequest. This service is not
    part of the application or plugin SDK facades.
    """

    def __init__(self, session: SessionLog) -> None:
        self.log = session
        self._values = session.plugin_instructions
        self._lock = asyncio.Lock()

    @property
    def values(self) -> dict[str, str]:
        return self._values.copy()

    async def set(self, name: str, value: str | None) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("Plugin instruction names must be non-empty strings")
        if value is not None and not isinstance(value, str):
            raise TypeError("Plugin instructions must be strings or None")
        async with self._lock:
            if self._values.get(name) == value:
                return
            event = PluginInstructionsChangedEvent(name, value)
            try:
                await self.log.append(event)
            finally:
                if self.log.last(PluginInstructionsChangedEvent) is event:
                    if value is None:
                        self._values.pop(name, None)
                    else:
                        self._values[name] = value

    async def remove(self, name: str) -> None:
        await self.set(name, None)
