from dataclasses import dataclass
from typing import assert_type

from harnessed import AgentLoop, FinalStepPolicy, Plugin, tool
from harnessed.hooks import ExecuteTool, Next, PreStep, ReviewToolResult, ToolPolicy
from harnessed.kernel import hook
from harnessed.models import ToolResult
from harnessed.tools import (
    Param,
    ToolAnnotations,
    ToolChain,
    ToolIcon,
    ToolInfo,
    ToolMetadata,
    ToolPresentation,
)


@dataclass(frozen=True)
class AuditTag:
    category: str


assert_type(ToolMetadata((AuditTag("workspace"),)).get(AuditTag), AuditTag | None)
AgentLoop(final_step=FinalStepPolicy.SUMMARIZE)


class Tools(Plugin):
    @tool(
        presentation=ToolPresentation(
            title="Switch workspace",
            tags={"workspace"},
            icons=(ToolIcon("https://example.com/icon.png", theme="light"),),
            annotations=ToolAnnotations(idempotent_hint=True),
            meta={"category": "workspace"},
        ),
        metadata=(AuditTag("workspace"),),
        chain=ToolChain.STOP_AFTER,
    )
    async def switch(self) -> ToolResult:
        return ToolResult("new context", break_chain=True)

    @hook(ExecuteTool)
    async def execute(self, event: ExecuteTool, next_: Next[ToolResult]) -> ToolResult:
        assert_type(event.tool, ToolInfo | None)
        if event.tool is not None:
            assert_type(event.tool.metadata.get(AuditTag), AuditTag | None)
            assert_type(event.tool.presentation, ToolPresentation)
            assert_type(event.tool.presentation.title, str | None)
            assert_type(event.tool.presentation.tags, frozenset[str])
            assert_type(event.tool.presentation.icons, tuple[ToolIcon, ...])
            assert_type(event.tool.presentation.annotations, ToolAnnotations | None)
        return await next_()


assert_type(Tools().with_tool(Tools.switch, description="Switch to another workspace"), Tools)
assert_type(Param(default=0.15, le=1), Param)
assert_type(Tools().without_tools(Tools.switch), Tools)
Tools().without_tools("switch")  # ty: ignore[invalid-argument-type]


def inspect_hooks(policy: ToolPolicy, review: ReviewToolResult, step: PreStep) -> None:
    assert_type(policy.tool, ToolInfo | None)
    assert_type(review.tool, ToolInfo | None)
    assert_type(step.progress.max_steps, int)
    assert_type(step.progress.remaining_steps, int)
    assert_type(step.progress.is_last_step, bool)
    assert_type(step.progress.is_final_step, bool)
