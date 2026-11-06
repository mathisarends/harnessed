from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from typing import Any, Unpack, overload

from harnessed.core.approvals import Approvals
from harnessed.core.children import ChildSessionRequest
from harnessed.core.composition import (
    CompositionDescription,
    PluginInput,
    PluginValidation,
    compose_plugins,
)
from harnessed.core.errors import ConfigError
from harnessed.core.events.registry import EventRegistry
from harnessed.core.model import Model
from harnessed.core.outputs import OutputContract, OutputTypes
from harnessed.core.plugin import Service, plugin_contribution, plugin_events
from harnessed.core.prompt.instructions import PromptRenderer
from harnessed.core.prompt.values import PromptComposition, SystemPrompt
from harnessed.core.result import RunResult
from harnessed.core.runtime.agent_loop import AgentLoop
from harnessed.core.runtime.opener import open_session
from harnessed.core.runtime.options import RunOptions
from harnessed.core.runtime.services import RuntimeServices
from harnessed.core.runtime.session import Session
from harnessed.core.session.state import SessionState
from harnessed.core.session.store import SessionStore
from harnessed.core.tools.output_limit import ToolOutputLimit
from harnessed.core.tracing import NoopTracer
from harnessed.kernel import PluginLike, Scope
from harnessed.models import PydanticAIModel


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
        self.plugin_validation = PluginValidation(plugin_validation)
        self.store = store
        supplied: list[PluginInput] = [value for value in plugins if value is not None]
        if prompt is not None:
            supplied.insert(0, PromptRenderer(prompt))
        if model is not None:
            supplied.insert(0, Service(Model, model))
        composition = compose_plugins(
            supplied,
            defaults=[
                RuntimeServices(),
                AgentLoop(),
                ToolOutputLimit(),
                Approvals(),
                NoopTracer(),
                PromptRenderer(),
            ],
        )
        self._description = composition.description
        normalized: list[PluginLike] = []
        for value in composition.plugins:
            if isinstance(value, Service) and value.key is Model:
                implementation = value.value
                if isinstance(implementation, str):
                    implementation = PydanticAIModel(implementation)
                elif not isinstance(implementation, Model):
                    raise ConfigError(
                        "model must implement harnessed.models.Model; wrap native "
                        "PydanticAI models with PydanticAIModel(model)"
                    )
                value = Service(Model, implementation)
            normalized.append(value)
        declared = next((value for value in normalized if isinstance(value, PromptRenderer)), None)
        self.prompt = PromptComposition(
            declared.values if declared is not None else None,
            (plugin_contribution(value) for value in normalized),
            instructions=instructions,
        )
        self.plugins = tuple(
            PromptRenderer(self.prompt.document, composition=self.prompt)
            if isinstance(value, PromptRenderer)
            else value
            for value in normalized
        )
        self._events = EventRegistry(
            kind for plugin in self.plugins for kind in plugin_events(plugin)
        )

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
        prompt: str,
        *,
        state: SessionState | None = None,
        output: type[T] | OutputContract[T],
        **options: Unpack[RunOptions],
    ) -> RunResult[T]: ...

    @overload
    async def run(
        self,
        prompt: str,
        *,
        state: SessionState | None = None,
        output: None = None,
        **options: Unpack[RunOptions],
    ) -> RunResult[None]: ...

    async def run(
        self,
        prompt: str,
        *,
        state: SessionState | None = None,
        output: type[Any] | OutputContract[Any] | None = None,
        **options: Unpack[RunOptions],
    ) -> RunResult[Any]:
        """Run one task in a new session."""
        async with self.session(state=state) as session:
            return await session.run(prompt, output=output, **options)
