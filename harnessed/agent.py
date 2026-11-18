from collections.abc import AsyncGenerator, Iterable, Sequence
from contextlib import asynccontextmanager
from itertools import chain
from typing import Any, Unpack, overload

from harnessed.errors import ConfigError
from harnessed.events.registry import EventRegistry
from harnessed.kernel import PluginLike, Scope
from harnessed.models import Model, UserMessage
from harnessed.models.pydantic_ai import PydanticAIModel
from harnessed.prompts.instructions import PromptRenderer
from harnessed.prompts.values import PromptComposition, SystemPrompt
from harnessed.runtime.agent_loop import AgentLoop
from harnessed.runtime.approval_policy import Approvals
from harnessed.runtime.child_contracts import ChildSessionRequest
from harnessed.runtime.composition import (
    CompositionDescription,
    PluginInput,
    PluginValidation,
    compose_plugins,
)
from harnessed.runtime.opener import open_session
from harnessed.runtime.options import RunOptions
from harnessed.runtime.outputs import OutputContract, OutputTypes
from harnessed.runtime.plugin import Service, plugin_contribution, plugin_events
from harnessed.runtime.services import RuntimeServices
from harnessed.runtime.session import Session
from harnessed.runtime.tool_output_limit import ToolOutputLimit
from harnessed.runtime.tracing import NoopTracer
from harnessed.sessions import SessionState, SessionStore
from harnessed.sessions.result import RunResult


class Agent:
    """Resolve model, instructions and plugins into a reusable configuration.

    Each opened session owns its runtime and plugin lifetimes. The configured
    conversation state is returned to the caller rather than archived by the agent.
    """

    def __init__(
        self,
        *,
        model: Model | str | None = None,
        instructions: str | None = None,
        prompt: SystemPrompt | None = None,
        plugins: Iterable[PluginInput | None] = (),
        plugin_validation: PluginValidation = PluginValidation.ERROR,
        store: SessionStore | None = None,
    ) -> None:
        self.plugin_validation = (  # smelt: ignore[SMT335] -- Public Agent setting.
            PluginValidation(plugin_validation)
        )
        self.store = store
        composition = compose_plugins(
            _configured_plugins(plugins, model=model, prompt=prompt),
            defaults=_default_plugins(),
        )
        self._description = composition.description
        resolved = [_resolve_model(value) for value in composition.plugins]
        self.prompt = _compose_prompt(resolved, instructions=instructions)
        self.plugins = tuple(_bind_prompt(value, self.prompt) for value in resolved)
        self._events = EventRegistry(chain.from_iterable(map(plugin_events, self.plugins)))

    def describe(self) -> CompositionDescription:
        """Inspect configured order, origins, replacements and exclusions without mounting."""
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
            await scope.install(self.plugins, settle=True)
            yield await open_session(
                scope, state, plugin_validation=self.plugin_validation, store=self.store, id=id
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


def _configured_plugins(
    plugins: Iterable[PluginInput | None],
    *,
    model: Model | str | None,
    prompt: SystemPrompt | None,
) -> list[PluginInput]:
    """Place the explicit model and prompt arguments before the supplied plugins."""
    leading: list[PluginInput] = []
    if model is not None:
        leading.append(Service(Model, model))
    if prompt is not None:
        leading.append(PromptRenderer(prompt))
    return [*leading, *(value for value in plugins if value is not None)]


def _default_plugins() -> list[PluginLike]:
    """Create the default runtime plugins that fill unclaimed slots."""
    return [
        RuntimeServices(),
        AgentLoop(),
        ToolOutputLimit(),
        Approvals(),
        NoopTracer(),
        PromptRenderer(),
    ]


def _resolve_model(value: PluginLike) -> PluginLike:
    """Replace a configured model name or object with a validated model service."""
    if not isinstance(value, Service) or value.key is not Model:
        return value
    implementation = value.value
    if isinstance(implementation, str):
        return Service(Model, PydanticAIModel(implementation))
    if isinstance(implementation, Model):
        return Service(Model, implementation)
    raise ConfigError(
        "model must implement harnessed.models.Model; wrap native "
        "PydanticAI models with PydanticAIModel(model)"
    )


def _compose_prompt(
    plugins: Sequence[PluginLike], *, instructions: str | None
) -> PromptComposition:
    """Compose the declared system prompt with every plugin's prompt contribution."""
    declared = next((value for value in plugins if isinstance(value, PromptRenderer)), None)
    values = None
    if declared is not None:
        values = declared.values
    return PromptComposition(
        values,
        (plugin_contribution(value) for value in plugins),
        instructions=instructions,
    )


def _bind_prompt(value: PluginLike, prompt: PromptComposition) -> PluginLike:
    """Bind the prompt renderer to the composed prompt; keep other plugins unchanged."""
    if isinstance(value, PromptRenderer):
        return PromptRenderer(prompt.document, composition=prompt)
    return value
