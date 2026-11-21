from harnessed.context.tool_output_limit import ToolOutputLimit
from harnessed.events.turn import FinalStepPolicy
from harnessed.kernel import Scope
from harnessed.models import Model, TokenLimitBehavior
from harnessed.sessions.log import SessionLog
from harnessed.tools import ErrorFormatter

from .options import AgentLoopConfig, AgentOptions
from .session import Session

_DEFAULTS = AgentLoopConfig()


class AgentLoop:
    """Settings of the built-in ReAct loop: steps, tool execution and output retries.

    The loop is part of the runtime core rather than a plugin. Pass an instance
    as ``Agent(loop=...)``; every default is visible in this signature.
    """

    def __init__(
        self,
        *,
        max_steps: int = _DEFAULTS.max_steps,
        tool_timeout_seconds: float = _DEFAULTS.tool_timeout_seconds,
        max_parallel_tools: int = _DEFAULTS.max_parallel_tools,
        on_max_tokens: TokenLimitBehavior = _DEFAULTS.on_max_tokens,
        output_retries: int = _DEFAULTS.output_retries,
        final_step: FinalStepPolicy = _DEFAULTS.final_step,
        error_formatter: ErrorFormatter | None = None,
    ) -> None:
        self.config = AgentLoopConfig(
            max_steps=max_steps,
            tool_timeout_seconds=tool_timeout_seconds,
            max_parallel_tools=max_parallel_tools,
            on_max_tokens=on_max_tokens,
            output_retries=output_retries,
            final_step=final_step,
        )
        self.error_formatter = error_formatter

    def __repr__(self) -> str:
        fields = ", ".join(f"{name}={value!r}" for name, value in self.config)
        return f"AgentLoop({fields})"

    @property
    def max_steps(self) -> int:
        return self.config.max_steps

    def create(self, scope: Scope, session: SessionLog) -> Session:
        """Build a live session for the supplied scope and conversation log."""
        # Require a model before opening the runtime, without caching its metadata.
        scope[Model]
        options = AgentOptions.resolve(
            self.config,
            tool_output_limit=scope[ToolOutputLimit],
            error_formatter=self.error_formatter,
        )
        return Session(scope, session, options)
