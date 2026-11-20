from harnessed.events import ModelRequestedEvent, ModelUsageRecordedEvent
from harnessed.kernel import PluginScope
from harnessed.models.model import UsagePurpose
from harnessed.prompts import SystemPromptStore
from harnessed.tools import ToolRegistry

from .hooks import SessionAppended
from .plugin import Plugin
from .tokens import CalibratedTokenCounter, TokenCounter


class RuntimeServices(Plugin):
    """Provide the tool, prompt and token services of the runtime core.

    Agent installs this before any configured plugin; it is not part of the
    plugin composition. The token counter calibrates itself against reported
    input tokens and can be replaced with ``Service(TokenCounter, value)``.
    """

    async def setup(self, scope: PluginScope) -> None:
        counter = CalibratedTokenCounter()
        scope.provide(ToolRegistry, ToolRegistry(scope))
        scope.provide(SystemPromptStore, SystemPromptStore(scope))
        scope.provide(TokenCounter, counter)

        def calibrate(event: SessionAppended) -> None:
            usage = event.event
            if (
                not isinstance(usage, ModelUsageRecordedEvent)
                or usage.purpose != UsagePurpose.RESPONSE
            ):
                return
            request = event.log.last(ModelRequestedEvent)
            if request is not None:
                counter.calibrate(request.request, usage.usage.input_tokens)

        scope.on(SessionAppended, calibrate)
