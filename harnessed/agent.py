from collections.abc import AsyncGenerator, Iterable, Iterator, Sequence
from contextlib import asynccontextmanager
from itertools import chain
from typing import Any, Unpack, overload

from harnessed.approvals import Approvals
from harnessed.approvals.responders import PARENT_APPROVER
from harnessed.children import ChildSessionRequest
from harnessed.context import Context
from harnessed.context.tool_output_limit import ToolOutputLimit
from harnessed.errors import ConfigError
from harnessed.events.registry import EventRegistry
from harnessed.kernel import Scope
from harnessed.models import Model, UserMessage
from harnessed.models.pydantic_ai import PydanticAIModel
from harnessed.output import OutputContract
from harnessed.output.contracts import OutputTypes
from harnessed.prompts import SystemPrompt
from harnessed.prompts.instructions import PromptRenderer
from harnessed.prompts.values import PromptComposition
from harnessed.runtime.agent_loop import AgentLoop
from harnessed.runtime.composition import (
    CompositionDescription,
    PluginValidation,
    compose_plugins,
)
from harnessed.runtime.opener import open_session
from harnessed.runtime.options import RunOptions
from harnessed.runtime.plugin import Plugin, Service, plugin_events
from harnessed.runtime.services import RuntimeServices
from harnessed.runtime.session import Session
from harnessed.sessions import CommitHandler, SessionCheckpoint, SessionState
from harnessed.sessions.result import RunResult

# A covariant sequence lets lists of concrete plugin types be nested.
type _PluginEntry = Plugin | Sequence[_PluginEntry]


class Agent:
    """Resolve model, instructions, loop settings and plugins into a reusable configuration.

    Nested lists and tuples in ``plugins`` are flattened in the supplied order.
    The session log, tool registry, prompt renderer and configured loop are
    runtime components. Each opened session owns its runtime and plugin lifetimes.
    The caller receives and owns the conversation state.
    """

    def __init__(
        self,
        *,
        model: Model | str | None = None,
        instructions: str | SystemPrompt | None = None,
        approvals: Approvals | None = None,
        context: Context | None = None,
        plugins: Iterable[_PluginEntry] = (),
        loop: AgentLoop | None = None,
        plugin_validation: PluginValidation = PluginValidation.ERROR,
    ) -> None:
        self.plugin_validation = (  # smelt: ignore[VIS004] -- Public Agent setting.
            PluginValidation(plugin_validation)
        )
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
        self.plugins = tuple(_flatten_plugins(plugins))
        self._description = compose_plugins(self.plugins, model=self.model, loop=loop)
        if instructions is not None and not isinstance(instructions, str | SystemPrompt):
            raise TypeError("instructions must be text or a SystemPrompt")
        self.instructions = instructions
        self.prompt = PromptComposition(
            instructions if isinstance(instructions, SystemPrompt) else None,
            (value.prompt_contribution() for value in self.plugins),
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
        state: SessionState | SessionCheckpoint | None = None,
        id: str | None = None,
        on_commit: CommitHandler | None = None,
        commit_timeout_seconds: float | None = None,
        restore_outputs: Iterable[type[Any] | OutputContract[Any]] = (),
    ) -> AsyncGenerator[Session]:
        """Open a live conversation, optionally continuing an application-owned value.

        ``state`` accepts a terminal ``SessionState`` or a ``SessionCheckpoint``,
        whose journal may contain an open turn; interrupted activity is repaired
        on open.
        ``on_commit`` receives every ``SessionCommit`` of this session and of the
        child sessions it delegates to, at durability barriers. Harnessed does
        not store conversations itself. The handler must not write to or flush
        its own session. ``commit_timeout_seconds`` bounds each delivery; an
        expired one fails the commit with ``CommitTimeout``. Child sessions
        share the deadline.
        """
        async with self._open_session(
            state=state,
            id=id,
            on_commit=on_commit,
            commit_timeout_seconds=commit_timeout_seconds,
            restore_outputs=restore_outputs,
        ) as session:
            yield session

    @asynccontextmanager
    async def child_session(self, request: ChildSessionRequest) -> AsyncGenerator[Session]:
        """Open a fresh isolated session for a parent-owned delegation."""
        async with self._open_session(
            id=request.session_id,
            on_commit=request.on_commit,
            commit_timeout_seconds=request.commit_timeout_seconds,
            child=request,
        ) as session:
            yield session

    @asynccontextmanager
    async def _open_session(
        self,
        *,
        state: SessionState | SessionCheckpoint | None = None,
        id: str | None = None,
        on_commit: CommitHandler | None = None,
        commit_timeout_seconds: float | None = None,
        restore_outputs: Iterable[type[Any] | OutputContract[Any]] = (),
        child: ChildSessionRequest | None = None,
    ) -> AsyncGenerator[Session]:
        async with Scope() as scope:
            if child is not None:
                scope.provide(ChildSessionRequest, child)
                scope.provide(PARENT_APPROVER, child.approver)
            scope.provide(EventRegistry, self._events.copy())
            # Validators are per session, so dynamic output types never leak between them.
            outputs = OutputTypes()
            for output in restore_outputs:
                outputs.register(output)
            scope.provide(OutputTypes, outputs)
            scope.provide(ToolOutputLimit, ToolOutputLimit(self.context.max_tool_output_chars))
            if self.model is not None:
                await scope.install([Service(Model, self.model)], settle=True)
            await self.context.install(scope.plugin_scope, resolve_model=_resolve_model)
            core = (
                RuntimeServices(),
                PromptRenderer(self.prompt.document, composition=self.prompt),
            )
            await scope.install(core, settle=True)
            self.approvals.install(scope.plugin_scope)
            pending = await scope.install_available(self.plugins)
            yield await open_session(
                scope,
                state,
                loop=self.loop,
                pending=pending,
                plugin_validation=self.plugin_validation,
                on_commit=on_commit,
                commit_timeout_seconds=commit_timeout_seconds,
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


def _flatten_plugins(plugins: Iterable[_PluginEntry]) -> Iterator[Plugin]:
    stack = [(plugins, iter(plugins))]
    active_groups = {id(plugins)}
    while stack:
        group, entries = stack[-1]
        try:
            entry = next(entries)
        except StopIteration:
            stack.pop()
            active_groups.remove(id(group))
            continue
        if isinstance(entry, Plugin):
            yield entry
        elif isinstance(entry, list | tuple):
            group_id = id(entry)
            if group_id in active_groups:
                raise TypeError("Plugin groups cannot contain themselves")
            stack.append((entry, iter(entry)))
            active_groups.add(group_id)
        else:
            raise TypeError("Expected a Plugin instance")


def _resolve_model(value: Model | str | None) -> Model | None:
    if isinstance(value, str):
        return PydanticAIModel(value)
    if value is None or isinstance(value, Model):
        return value
    raise ConfigError(
        "model must implement harnessed.models.Model; wrap native models with PydanticAIModel"
    )
