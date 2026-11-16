from pydantic import BaseModel

from harnessed.core.plugin import Plugin
from harnessed.core.tools import (
    EnvelopePosition,
    EnvelopeSelector,
    InvalidEnvelope,
    ToolRegistry,
    envelope_models,
    envelope_selector,
)
from harnessed.kernel import PluginScope


class CallEnvelope(Plugin, needs=(ToolRegistry,)):
    """Ask the model for an envelope model's fields on every selected tool call.

    The fields are merged into the definitions of tools owned by any plugin, MCP
    server or external integration, registered before or after this plugin, and
    are removed before the tool runs. Hooks read the validated values through
    `event.envelope.get(model)`; event consumers use `envelope_value(call, model)`.
    The fields disappear from definitions on the next request after unmount.

    `select` receives each tool's declared `ToolInfo`; the default selects every
    tool. With the default `InvalidEnvelope.DROP`, a call that omits or malforms
    the fields still runs without the envelope value. `position` places the fields
    before (`LEADING`, the default) or after (`TRAILING`) the tool's parameters.
    """

    def __init__(
        self,
        model: type[BaseModel],
        *,
        select: EnvelopeSelector | None = None,
        on_invalid: InvalidEnvelope = InvalidEnvelope.DROP,
        position: EnvelopePosition = EnvelopePosition.LEADING,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        (self._model,) = envelope_models((model,))
        self._select = envelope_selector(select)
        self._on_invalid = InvalidEnvelope(on_invalid)
        self._position = EnvelopePosition(position)

    async def setup(self, scope: PluginScope) -> None:
        scope[ToolRegistry].envelope(
            self._model,
            select=self._select,
            on_invalid=self._on_invalid,
            position=self._position,
            owner=scope,
        )
