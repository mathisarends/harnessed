from harnessed.core.plugin import Plugin
from harnessed.core.prompt.system_prompt import SystemPromptStore
from harnessed.core.prompt.values import PromptComposition, SystemPrompt
from harnessed.kernel import Context


class PromptRenderer(Plugin, slot="instructions", needs=(SystemPromptStore,)):
    """Render literal instructions and file sections once when a session opens."""

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

    async def setup(self, ctx: Context) -> None:
        composition = self.composition or PromptComposition(self.values)
        ctx[SystemPromptStore].fixed(await composition.render(ctx), owner=ctx)
