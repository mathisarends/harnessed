from harnessed.core.approvals import ApprovalsConfig
from harnessed.core.budget import ContextBudget
from harnessed.core.events.model import ModelRequestedEvent, ModelUsageRecordedEvent
from harnessed.core.hooks import SessionAppended
from harnessed.core.model import UsagePurpose
from harnessed.core.plugin import Plugin
from harnessed.core.prompt.system_prompt import SystemPromptStore
from harnessed.core.tokens import CalibratedTokenCounter, TokenCounter
from harnessed.core.tools import ToolRegistry
from harnessed.kernel import Scope


class RuntimeServices(Plugin):
    """Provide the tools, prompt and token services every loop shares.

    Agent always installs this first, so replacing the ``loop`` slot keeps the
    tools. Each service can still be replaced with ``Service(Key, value)``. The
    default token counter calibrates itself against reported input tokens.
    """

    async def setup(self, scope: Scope) -> None:
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


__all__ = ["RuntimeServices"]
