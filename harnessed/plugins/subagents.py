from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated

from pydantic import Field

from harnessed.core.child_status import ChildActivity as ChildActivity
from harnessed.core.child_status import ChildReport as ChildReport
from harnessed.core.child_status import ChildReportState as ChildReportState
from harnessed.core.child_status import ChildRuntimeState as ChildRuntimeState
from harnessed.core.children import ChildLimits
from harnessed.core.children import ChildSessionFactory as ChildSessionFactory
from harnessed.core.children import ChildSessionRequest as ChildSessionRequest
from harnessed.core.children import ChildStatusReporter as ChildStatusReporter
from harnessed.core.hooks import SessionAborting, SessionClosing
from harnessed.core.messages import ToolResult
from harnessed.core.plugin import Plugin, PluginConfig
from harnessed.core.prompt.values import SystemPrompt
from harnessed.core.runtime.child_execution import ChildParent
from harnessed.core.runtime.children import ChildSessionSupervisor
from harnessed.core.runtime.context import SessionContext
from harnessed.core.runtime.control import SessionControl
from harnessed.core.session.children import ChildSession as ChildSession
from harnessed.core.tools import ToolContext, tool
from harnessed.kernel import Inject, PluginScope, needs


class SubagentsConfig(PluginConfig):
    max_parallel: Annotated[int, Field(ge=1)] = 3
    max_calls: Annotated[int, Field(ge=1)] = 16
    max_depth: Annotated[int, Field(ge=1)] = 1
    timeout: Annotated[float, Field(gt=0, le=3600)] = 180.0
    max_steps: Annotated[int, Field(ge=1)] = 8
    token_budget: Annotated[int, Field(ge=1)] | None = None


_DEFAULTS = SubagentsConfig()


class Subagents(Plugin[SubagentsConfig]):
    """Delegate explicit tasks to configured agents in isolated child sessions."""

    session = needs(SessionContext)
    control = needs(SessionControl)

    def __init__(
        self,
        agents: Mapping[str, ChildSessionFactory],
        *,
        max_parallel: int = _DEFAULTS.max_parallel,
        max_calls: int = _DEFAULTS.max_calls,
        max_depth: int = _DEFAULTS.max_depth,
        timeout: float = _DEFAULTS.timeout,
        max_steps: int = _DEFAULTS.max_steps,
        token_budget: int | None = _DEFAULTS.token_budget,
    ) -> None:
        super().__init__(
            config=SubagentsConfig(
                max_parallel=max_parallel,
                max_calls=max_calls,
                max_depth=max_depth,
                timeout=timeout,
                max_steps=max_steps,
                token_budget=token_budget,
            )
        )
        if not agents or any(not isinstance(name, str) or not name.strip() for name in agents):
            raise ValueError("Subagents requires nonempty agent names")
        if any(not isinstance(agent, ChildSessionFactory) for agent in agents.values()):
            raise TypeError("Subagents requires ChildSessionFactory implementations such as Agent")
        self.agents = MappingProxyType(dict(agents))

    def instructions(self) -> SystemPrompt:
        return SystemPrompt(
            "Use spawn(agent, task) to start a specialist and continue working. "
            "Use status(child_id, timeout) to ask it for a fresh report, send(child_id, message) "
            "to steer it, wait(child_id, timeout) for its result, and cancel(child_id) to stop it. "
            "Use delegate(agent, task) when you want to wait until completion immediately. "
            "Spawned tasks send completion notifications to your inbox. "
            "Each child has its own instructions and tools and receives only the supplied task. "
            f"Available agents: {', '.join(self.agents)}."
        )

    async def setup(self, scope: PluginScope) -> None:
        limits = ChildLimits(
            self.config.max_parallel,
            self.config.max_calls,
            self.config.max_depth,
            self.config.timeout,
            self.config.max_steps,
            self.config.token_budget,
        )
        self._supervisor = ChildSessionSupervisor(
            ChildParent(self.session, self.control, scope), self.agents, limits
        )
        scope.effect(self._supervisor.aclose)
        scope.on(SessionAborting, self._supervisor.aborting)
        scope.on(SessionClosing, self._supervisor.closing)

    @tool(timeout_seconds=3600.0, parallel=True)
    async def delegate(self, agent: str, task: str, *, context: Inject[ToolContext]) -> ToolResult:
        """Run a specialist and return its child ID, answer, status and usage."""
        return await self._supervisor.delegate(agent, task, context)

    @tool(parallel=True)
    async def spawn(self, agent: str, task: str, *, context: Inject[ToolContext]) -> ToolResult:
        """Start a specialist and return its child_id without waiting for its task."""
        return await self._supervisor.spawn(agent, task, context)

    @tool(timeout_seconds=3600.0, parallel=True, readonly=True)
    async def status(self, child_id: str, timeout: float = 10.0) -> ToolResult:
        """Ask the child for a fresh report; timeout returns pending without cancelling it."""
        return await self._supervisor.status(child_id, timeout)

    @tool(parallel=True)
    async def send(
        self, child_id: str, message: Annotated[str, Field(min_length=1, max_length=32768)]
    ) -> ToolResult:
        """Deliver additional guidance at the child's next step without starting a new task."""
        return await self._supervisor.send(child_id, message)

    @tool(timeout_seconds=3600.0, parallel=True, readonly=True)
    async def wait(self, child_id: str, timeout: float = 30.0) -> ToolResult:
        """Wait for a child receipt; timeout returns current state and leaves the child running."""
        return await self._supervisor.wait(child_id, timeout)

    @tool(timeout_seconds=3600.0, parallel=True)
    async def cancel(self, child_id: str) -> ToolResult:
        """Cancel a child and wait until its driver and plugin resources are drained."""
        return await self._supervisor.cancel(child_id)


__all__ = [
    "ChildActivity",
    "ChildReport",
    "ChildReportState",
    "ChildRuntimeState",
    "ChildSession",
    "ChildSessionFactory",
    "ChildSessionRequest",
    "ChildStatusReporter",
    "Subagents",
]
