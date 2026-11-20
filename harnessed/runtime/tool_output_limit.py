from dataclasses import replace
from typing import Annotated

from pydantic import Field

from harnessed.models.messages import OutputTruncation, ToolResult

from .plugin import PluginConfig


class ToolOutputLimitConfig(PluginConfig):
    max_chars: Annotated[int, Field(ge=256)] = 32_000


_DEFAULTS = ToolOutputLimitConfig()


class ToolOutputLimit:
    """Bound final tool results before they enter the durable log and model history.

    The session-wide limit is ``AgentLoop(max_tool_output=...)``; tools may
    declare their own limit for individual results.
    """

    def __init__(self, max_chars: int = _DEFAULTS.max_chars) -> None:
        self.max_chars = ToolOutputLimitConfig(max_chars=max_chars).max_chars

    def apply(self, result: ToolResult) -> ToolResult:
        text = result.text
        if len(text) <= self.max_chars:
            return result
        notice = (
            f"\n\n[Output truncated; middle omitted ({len(text)} characters total). "
            "Use offset/limit for smaller ranges or filter the command output.]\n\n"
        )
        retained = self.max_chars - len(notice)
        head = retained * 3 // 4
        tail = retained - head
        return replace(
            result.with_text(text[:head] + notice + (text[-tail:] if tail else "")),
            truncation=OutputTruncation(len(text), retained, self.max_chars),
        )
