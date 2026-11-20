from harnessed.kernel import PluginScope
from harnessed.runtime.plugin import Plugin

from .system import SystemPromptStore
from .values import PromptComposition, SystemPrompt


class PromptRenderer(Plugin, needs=(SystemPromptStore,)):
    """Render the agent's composed prompt once when a session opens.

    Part of the runtime core: Agent installs it from ``instructions=``/``prompt=``.
    """

    def __init__(
        self,
        values: SystemPrompt | None = None,
        *,
        composition: PromptComposition | None = None,
    ) -> None:
        super().__init__()
        if values is not None and not isinstance(values, SystemPrompt):
            raise TypeError("PromptRenderer requires a SystemPrompt")
        self.values = values
        self.composition = composition

    async def setup(self, scope: PluginScope) -> None:
        composition = self.composition or PromptComposition(self.values)
        rendered = await composition.render(scope)
        scope[SystemPromptStore].fixed(rendered, owner=scope)
