from __future__ import annotations

from typing import Protocol

from harnessed.core.limits import FinalStepPolicy
from harnessed.core.model import Model, TokenLimitBehavior
from harnessed.core.plugin import Plugin
from harnessed.core.runtime.options import AgentLoopConfig, AgentOptions
from harnessed.core.runtime.session import Session
from harnessed.core.session.session import SessionLog
from harnessed.core.tools.output_limit import ToolOutputLimit
from harnessed.core.tools.registry import ErrorFormatter
from harnessed.kernel import Scope


class AgentFactory(Protocol):
    def create(self, scope: Scope, session: SessionLog) -> Session: ...


_DEFAULTS = AgentLoopConfig()


class AgentLoop(Plugin[AgentLoopConfig], slot="loop"):
    """Configure the ReAct loop: step, tool execution and output limits."""

    def __init__(
        self,
        *,
        max_steps: int = _DEFAULTS.max_steps,
        tool_timeout: float = _DEFAULTS.tool_timeout,
        max_parallel_tools: int = _DEFAULTS.max_parallel_tools,
        context_window: int | None = _DEFAULTS.context_window,
        on_max_tokens: TokenLimitBehavior = _DEFAULTS.on_max_tokens,
        output_retries: int = _DEFAULTS.output_retries,
        final_step: FinalStepPolicy = _DEFAULTS.final_step,
        error_formatter: ErrorFormatter | None = None,
    ) -> None:
        super().__init__(
            config=AgentLoopConfig(
                max_steps=max_steps,
                tool_timeout=tool_timeout,
                max_parallel_tools=max_parallel_tools,
                context_window=context_window,
                on_max_tokens=on_max_tokens,
                output_retries=output_retries,
                final_step=final_step,
            )
        )
        self.error_formatter = error_formatter

    async def setup(self, scope: Scope) -> None:
        scope.provide(AgentFactory, ReactLoopFactory(self.config, self.error_formatter))


class ReactLoopFactory:
    def __init__(self, config: AgentLoopConfig, error_formatter: ErrorFormatter | None) -> None:
        self.config = config
        self.error_formatter = error_formatter

    def create(self, scope: Scope, session: SessionLog) -> Session:
        # Require a model before opening the runtime, without caching its metadata.
        scope[Model]
        options = AgentOptions.resolve(
            self.config,
            tool_output_limit=scope[ToolOutputLimit],
            error_formatter=self.error_formatter,
        )
        return Session(scope, session, options)
