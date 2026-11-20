from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from itertools import chain
from typing import Any, Unpack, overload

from harnessed.context import Context
from harnessed.errors import ConfigError
from harnessed.events.registry import EventRegistry
from harnessed.kernel import Scope
from harnessed.models import Model, UserMessage
from harnessed.models.pydantic_ai import PydanticAIModel
from harnessed.prompts import SystemPrompt
from harnessed.prompts.instructions import PromptRenderer
from harnessed.prompts.values import PromptComposition
from harnessed.runtime.agent_loop import AgentLoop
from harnessed.runtime.approval_policy import Approvals
from harnessed.runtime.child_contracts import ChildSessionRequest
from harnessed.runtime.composition import (
    CompositionDescription,
    PluginValidation,
    compose_plugins,
)
from harnessed.runtime.opener import open_session
from harnessed.runtime.options import RunOptions
from harnessed.runtime.outputs import OutputContract, OutputTypes
from harnessed.runtime.plugin import Plugin, Service, plugin_contribution, plugin_events
from harnessed.runtime.services import RuntimeServices
from harnessed.runtime.session import Session
from harnessed.runtime.tool_output_limit import ToolOutputLimit
from harnessed.sessions import SessionState, SessionStore
from harnessed.sessions.result import RunResult


class Agent:
    """Resolve model, instructions, loop settings and plugins into a reusable configuration.

    The composition is exactly the supplied plugin list: nothing is added,
    replaced or removed implicitly. The runtime core (session log, tool
    registry, prompt rendering and the loop configured by ``loop``) is not a
    plugin. Each opened session owns its runtime and plugin lifetimes. The
    conversation state is returned to the caller rather than archived by the agent.
    """

    def __init__(
        self,
        *,
        model: Model | str | None = None,
        instructions: str | SystemPrompt | None = None,
        approvals: Approvals | None = None,
        context: Context | None = None,
        plugins: Iterable[Plugin] = (),
        loop: AgentLoop | None = None,
        plugin_validation: PluginValidation = PluginValidation.ERROR,
        store: SessionStore | None = None,
    ) -> None:
        self.plugin_validation = (  # smelt: ignore[SMT335] -- Public Agent setting.
            PluginValidation(plugin_validation)
        )
        self.store = store
        if loop is None:
            loop = AgentLoop()
        if not isinstance(loop, AgentLoop):
            raise ConfigError("loop must be an AgentLoop")
        self.loop = loop
        self.model = _resolve_model(model)
        self.context = Context() if context is None else context
        self.approvals = Approvals() if approvals is None else approvals
        if not isinstance(self.context, Context) or not isinstance(self.approvals, Approvals):
            raise ConfigError("Expected Context and Approvals settings")
        self.plugins = tuple(plugins)
        self._description = compose_plugins(self.plugins, model=self.model, loop=loop)
        if instructions is not None and not isinstance(instructions, str | SystemPrompt):
            raise TypeError("instructions must be text or a SystemPrompt")
        self.instructions = instructions
        self.prompt = PromptComposition(
            instructions if isinstance(instructions, SystemPrompt) else None,
            (plugin_contribution(value) for value in self.plugins),
            instructions=instructions if isinstance(instructions, str) else None,
        )
        self._events = EventRegistry(chain.from_iterable(map(plugin_events, self.plugins)))

    def describe(self) -> CompositionDescription:
        """Inspect the configured plugins and their declarations without mounting."""
        return self._description

    @asynccontextmanager
    async def session(
        self,
        *,
        state: SessionState | None = None,
        id: str | None = None,
        restore_outputs: Iterable[type[Any] | OutputContract[Any]] = (),
    ) -> AsyncGenerator[Session]:
        """Open a live conversation, optionally initialized from an immutable state."""
        async with self._open_session(
            state=state, id=id, restore_outputs=restore_outputs
        ) as session:
            yield session

    @asynccontextmanager
    async def child_session(self, request: ChildSessionRequest) -> AsyncGenerator[Session]:
        """Open a fresh isolated session for a parent-owned delegation."""
        async with self._open_session(id=request.session_id, child=request) as session:
            yield session

    @asynccontextmanager
    async def _open_session(
        self,
        *,
        state: SessionState | None = None,
        id: str | None = None,
        restore_outputs: Iterable[type[Any] | OutputContract[Any]] = (),
        child: ChildSessionRequest | None = None,
    ) -> AsyncGenerator[Session]:
        async with Scope() as scope:
            if child is not None:
                scope.provide(ChildSessionRequest, child)
            scope.provide(EventRegistry, self._events.copy())
            # Validators are per session, so dynamic output types never leak between them.
            outputs = OutputTypes()
            for output in restore_outputs:
                outputs.register(output)
            scope.provide(OutputTypes, outputs)
            scope.provide(ToolOutputLimit, ToolOutputLimit(self.context.max_tool_output_chars))
            if self.model is not None:
                await scope.install([Service(Model, self.model)], settle=True)
            await self.context.install(scope.plugin_scope)
            core = (
                RuntimeServices(),
                PromptRenderer(self.prompt.document, composition=self.prompt),
            )
            await scope.install(core, settle=True)
            self.approvals.install(scope.plugin_scope)
            await scope.install(self.plugins, settle=True)
            yield await open_session(
                scope,
                state,
                loop=self.loop,
                plugin_validation=self.plugin_validation,
                store=self.store,
                id=id,
            )

    @overload
    async def run[T](
        self,
        content: str | UserMessage,
        *,
        state: SessionState | None = None,
        output: type[T] | OutputContract[T],
        **options: Unpack[RunOptions],
    ) -> RunResult[T]: ...

    @overload
    async def run(
        self,
        content: str | UserMessage,
        *,
        state: SessionState | None = None,
        output: None = None,
        **options: Unpack[RunOptions],
    ) -> RunResult[None]: ...

    async def run(
        self,
        content: str | UserMessage,
        *,
        state: SessionState | None = None,
        output: type[Any] | OutputContract[Any] | None = None,
        **options: Unpack[RunOptions],
    ) -> RunResult[Any]:
        """Run one task in a new session."""
        async with self.session(state=state) as session:
            return await session.run(content, output=output, **options)


def _resolve_model(value: Model | str | None) -> Model | None:
    if isinstance(value, str):
        return PydanticAIModel(value)
    if value is None or isinstance(value, Model):
        return value
    raise ConfigError(
        "model must implement harnessed.models.Model; wrap native models with PydanticAIModel"
    )
