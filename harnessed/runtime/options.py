from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Self, TypedDict

from pydantic import Field

from harnessed.events.turn import FinalStepPolicy
from harnessed.models import GenerationSettings, TokenLimitBehavior
from harnessed.tools import ErrorFormatter

from .attachments import Attachment
from .plugin import PluginConfig
from .tool_output_limit import ToolOutputLimit


class RunOptions(TypedDict, total=False):
    """Per-turn inputs shared by Agent.run and Session.run/stream."""

    attachments: Iterable[Attachment]
    settings: GenerationSettings | None
    max_steps: int | None
    token_budget: int | None


class AgentLoopConfig(PluginConfig):
    """Validated loop settings; the single source of their defaults."""

    max_steps: Annotated[int, Field(ge=1)] = 32
    tool_timeout: Annotated[float, Field(gt=0)] = 30.0
    max_parallel_tools: Annotated[int, Field(ge=1)] = 4
    context_window: Annotated[int, Field(ge=1)] | None = None
    on_max_tokens: TokenLimitBehavior = TokenLimitBehavior.ACCEPT
    output_retries: Annotated[int, Field(ge=0)] = 2
    final_step: FinalStepPolicy = FinalStepPolicy.NONE
    max_tool_output: Annotated[int, Field(ge=256)] = 32_000


@dataclass(frozen=True, slots=True)
class AgentOptions:
    """Session loop settings; provider metadata is resolved for each request."""

    max_steps: int
    tool_timeout: float
    context_window: int | None
    tool_output_limit: ToolOutputLimit
    max_parallel_tools: int
    error_formatter: ErrorFormatter | None
    on_max_tokens: TokenLimitBehavior
    output_retries: int
    final_step: FinalStepPolicy

    @classmethod
    def resolve(
        cls,
        config: AgentLoopConfig,
        *,
        error_formatter: ErrorFormatter | None = None,
    ) -> Self:
        return cls(
            max_steps=config.max_steps,
            tool_timeout=config.tool_timeout,
            context_window=config.context_window,
            tool_output_limit=ToolOutputLimit(config.max_tool_output),
            max_parallel_tools=config.max_parallel_tools,
            error_formatter=error_formatter,
            on_max_tokens=config.on_max_tokens,
            output_retries=config.output_retries,
            final_step=config.final_step,
        )
