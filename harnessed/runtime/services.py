from harnessed.events import ModelRequestedEvent, ModelUsageRecordedEvent
from harnessed.kernel import PluginScope
from harnessed.models.model import UsagePurpose
from harnessed.prompts import SystemPromptStore
from harnessed.tools import ToolRegistry

from .approval_policy import ApprovalsConfig
from .budget import ContextBudget
from .hooks import SessionAppended
from .plugin import Plugin
from .tokens import CalibratedTokenCounter, TokenCounter


class RuntimeServices(Plugin):
    """Provide the tools, prompt and token services every loop shares.

    Agent always installs this first, so replacing the ``loop`` slot keeps the
    tools. Each service can still be replaced with ``Service(Key, value)``. The
    default token counter calibrates itself against reported input tokens.
    """

    async def setup(self, scope: PluginScope) -> None:
        counter = CalibratedTokenCounter()
        scope.provide(ApprovalsConfig, ApprovalsConfig())
        scope.provide(ToolRegistry, ToolRegistry(scope))
        scope.provide(SystemPromptStore, SystemPromptStore(scope))
        scope.provide(TokenCounter, counter)
        scope.provide(ContextBudget, ContextBudget())

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
