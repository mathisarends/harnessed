from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from copy import copy
from types import get_original_bases
from typing import Any, ClassVar, Self, TypeVar, cast, get_args, get_origin, overload

from pydantic import BaseModel, ConfigDict

from harnessed.events import DurableEvent, TransientEvent
from harnessed.kernel import (
    Key,
    PluginScope,
    PluginSpec,
    Scope,
    as_plugin_spec,
    binding_plan,
)
from harnessed.kernel import PluginBase as KernelPlugin
from harnessed.models import Model
from harnessed.prompts import SystemPrompt
from harnessed.prompts.values import PromptContribution
from harnessed.tools import (
    Param,
    ToolOverride,
    ToolReference,
    ToolRegistry,
    ToolSpec,
    activation_tool,
)
from harnessed.tools.references import declared_arguments, tool_reference


class PluginConfig(BaseModel):
    """Default external configuration policy; plugin schemas may extend it."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class Plugin[ConfigT: BaseModel](KernelPlugin):
    """A configured recipe reconstructed for each activation.

    Declare lifetime service bindings with needs()/observes() and mutable runtime data
    with PluginState. Constructors describe capabilities; setup acquires resources
    and registers cleanup on its PluginScope. Agent composition collects instructions.
    """

    config: ConfigT
    events: tuple[type[DurableEvent | TransientEvent], ...] = ()
    _configuration_type: ClassVar[type[BaseModel] | None] = None

    @overload
    def __init__(
        self: Plugin[PluginConfig],
        *,
        config: None = None,
        name: str | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        config: ConfigT,
        name: str | None = None,
    ) -> None: ...

    def __init__(
        self,
        *,
        config: ConfigT | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        schema = self._configuration_type
        if config is None:
            if schema is not None and schema is not PluginConfig:
                raise TypeError(f"{type(self).__name__} requires config={schema.__name__}(...)")
            # The no-config overload and runtime schema check select the empty model.
            config = cast(ConfigT, PluginConfig())
        if not isinstance(config, BaseModel):
            raise TypeError("config must be a frozen Pydantic model instance")
        if schema is not None and not isinstance(config, schema):
            raise TypeError(f"{type(self).__name__} requires a {schema.__name__} config instance")
        if not config.model_config.get("frozen"):
            raise TypeError("Plugin Config models must be frozen; inherit PluginConfig")
        self.config = config
        self._excluded_tools: frozenset[ToolReference] = frozenset()
        self._tool_overrides: dict[Callable[..., Any], ToolOverride] = {}
        self._instruction_override: SystemPrompt | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "Config" in vars(cls):
            raise TypeError("Config aliases were removed; declare Plugin[YourConfig] instead")
        cls._configuration_type = cls._configuration_schema()
        members = vars(cls)
        if "instructions" in members:
            provider = members["instructions"]
            if (
                not callable(provider)
                or inspect.iscoroutinefunction(provider)
                or inspect.isasyncgenfunction(provider)
            ):
                raise TypeError("instructions() must be a synchronous content provider")
        for name, member in tuple(vars(cls).items()):
            if callable(member) and isinstance(getattr(member, "_harnessed_tool", None), ToolSpec):
                if name == "instructions":
                    raise TypeError("instructions() is reserved for prompt content, not a tool")
                setattr(cls, name, activation_tool(member))

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "config" and "config" in vars(self):
            raise AttributeError("Plugin configuration cannot be replaced after construction")
        super().__setattr__(name, value)

    def create_instance(self) -> Self:
        """Reconstruct runtime attributes while retaining the validated frozen configuration."""
        if "config" not in vars(self):
            raise TypeError("Plugin constructors must call super().__init__()")
        instance: Self = super().create_instance()
        object.__setattr__(instance, "config", self.config)
        instance._instruction_override = self._instruction_override
        return instance

    def with_instructions(self, instructions: str | SystemPrompt) -> Self:
        """Return a configured copy with a replacement prompt block.

        An empty SystemPrompt removes the block.
        """
        if not isinstance(instructions, str | SystemPrompt):
            raise TypeError("instructions must be text or a SystemPrompt")
        document = SystemPrompt(instructions) if isinstance(instructions, str) else instructions
        if document.collects_plugins:
            raise ValueError("Plugin instructions must supply content, not plugin collection")
        configured: Self = self._configured_copy()
        configured._instruction_override = document
        return configured

    def without_tools(self, *tools: Callable[..., Any] | ToolReference) -> Self:
        """Return a recipe copy excluding the selected declared tools from registration."""
        configured: Self = self._configured_copy()
        excluded = set(self._excluded_tools)
        for tool in tools:
            excluded.add(self._declared_reference(tool))
        configured._excluded_tools = frozenset(excluded)
        return configured

    def with_tool(
        self,
        tool: Callable[..., Any] | ToolReference,
        *,
        description: str | None = None,
        parameters: Mapping[str, Param] | None = None,
    ) -> Self:
        """Return a recipe copy with updated settings for one declared tool."""
        configured: Self = self._configured_copy()
        ref = self._declared_reference(tool)
        settings = self.tool_override(ref.declaration).updated(description, parameters)
        if settings.parameters:
            form = declared_arguments(ref)
            form.configured(settings, tool_name=f"{type(self).__name__}.{ref.name}")
        configured._tool_overrides = self._tool_overrides | {ref.declaration: settings}
        return configured

    def tool_override(self, declaration: Callable[..., Any]) -> ToolOverride:
        """Read the settings for a tool declaration on this recipe."""
        return self._tool_overrides.get(declaration, ToolOverride())

    def without_instructions(self) -> Self:
        """Return a configured copy that contributes no prompt block."""
        return self.with_instructions(SystemPrompt())

    def instructions(self) -> SystemPrompt:
        """Return pure configuration-derived content, collected once per selected recipe.

        Override this single method to provide a complete instruction block.
        Session-owned files and factories may be returned as Instructions values.
        """
        return SystemPrompt()

    def prompt_contribution(self) -> PromptContribution:
        """Normalize this recipe's prompt metadata for the runtime."""
        name = as_plugin_spec(self).name
        override = self._instruction_override
        if override is not None:
            provider = (lambda: override) if override.sections else None
        else:
            provider = (
                self.instructions if type(self).instructions is not Plugin.instructions else None
            )
        return PromptContribution(name, provider)

    async def setup(self, scope: PluginScope) -> None:
        """Acquire resources and publish services for this activation.

        Dependencies and PluginState are bound first. Publications become visible
        to other plugins after setup and declared registrations succeed. Failure
        or cancellation rolls back owned resources and registrations.
        """

    async def teardown(self, scope: PluginScope) -> None:
        """Finish a successful activation before its owned cleanup runs.

        Dependents stop first. State and dependencies remain bound through cleanup.
        This method is skipped when setup fails; register partial-acquisition
        cleanup immediately with scope.enter(), effect() or register().
        """

    def _declared_reference(self, tool: Callable[..., Any] | ToolReference) -> ToolReference:
        reference = tool_reference(tool)
        declarations = (
            member
            for _, member in inspect.getmembers_static(type(self))
            if callable(member) and isinstance(getattr(member, "_harnessed_tool", None), ToolSpec)
        )
        if reference.declaration not in declarations or (
            reference.owner is not None and reference.owner is not self
        ):
            raise ValueError(f"Tool {reference.name!r} does not belong to this plugin recipe")
        return tool_reference(reference.declaration)

    def _configured_copy(self) -> Self:
        if self.recipe is not self:
            raise TypeError("Configure tools and instructions on the plugin recipe")
        configured: Self = copy(self)
        if (
            configured is self
            or type(configured) is not type(self)
            or configured.recipe is not configured
        ):
            raise TypeError("Copying a plugin recipe must return a fresh recipe of the same class")
        return configured

    @classmethod
    def _configuration_schema(cls) -> type[BaseModel] | None:
        """Resolve model parameters through generic bases without evaluating annotations."""
        models = tuple(dict.fromkeys(Plugin._configuration_models(cls, {})))
        if not models:
            return None
        selected = next(
            (model for model in models if all(issubclass(model, other) for other in models)), None
        )
        if selected is None:
            raise TypeError("Plugin bases declare incompatible configuration models")
        return selected

    # Subclass creation in this module resolves configuration before module helpers exist.
    @staticmethod
    def _configuration_models(
        base: object, substitutions: dict[TypeVar, object]
    ) -> list[type[BaseModel]]:
        """Configuration models declared by Plugin[...] along a plugin's generic bases."""
        origin = get_origin(base) or base
        if not isinstance(origin, type) or not issubclass(origin, KernelPlugin):
            return []
        arguments = get_args(base)
        if origin is Plugin:
            if not arguments:
                return []
            model = Plugin._resolve_type_argument(arguments[0], substitutions)
            if model is BaseModel:
                return []
            if not isinstance(model, type) or not issubclass(model, BaseModel):
                raise TypeError("Plugin[...] requires a Pydantic configuration model")
            return [model]
        bindings = dict(substitutions)
        parameters = vars(origin).get("__parameters__", ())
        for parameter, argument in zip(parameters, arguments, strict=bool(arguments)):
            bindings[parameter] = Plugin._resolve_type_argument(argument, substitutions)
        return [
            model
            for parent in get_original_bases(origin)
            for model in Plugin._configuration_models(parent, bindings)
        ]

    @staticmethod
    def _resolve_type_argument(value: object, substitutions: dict[TypeVar, object]) -> object:
        while isinstance(value, TypeVar) and value in substitutions:
            replacement = substitutions[value]
            if replacement is value:
                break
            value = replacement
        if isinstance(value, TypeVar):
            return value.__bound__
        return value

    def _as_plugin_spec(self) -> PluginSpec:
        for name, callback in (("setup", self.setup), ("teardown", self.teardown)):
            plan = binding_plan(callback)
            if plan.dependencies:
                raise TypeError(
                    f"{type(self).__name__}.{name}() cannot use Inject parameters; "
                    "declare plugin service fields with needs(T) or observes(T)"
                )
            parameters = tuple(plan.signature.parameters.values())
            if (
                not inspect.iscoroutinefunction(callback)
                or len(parameters) != 1
                or parameters[0].kind
                not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ):
                raise TypeError(
                    f"{type(self).__name__}.{name}() must be async and accept "
                    "one scope: PluginScope parameter"
                )
        return super()._as_plugin_spec()

    async def _setup(self, scope: Scope) -> None:
        await self.setup(scope.plugin_scope)
        recipe: Self = self.recipe
        excluded = recipe._excluded_tools
        if excluded:
            scope[ToolRegistry].exclude(excluded, owner=scope)

    async def _teardown(self, scope: Scope) -> None:
        await self.teardown(scope.plugin_scope)


def plugin_events(value: Plugin) -> tuple[type[DurableEvent | TransientEvent], ...]:
    """Event types a plugin declares; only core Plugin recipes declare any."""
    return value.events


class Service[T](Plugin):
    """Provide a typed instance as a normal, reversible plugin."""

    def __init__(self, key: type[T] | Key[T], value: T) -> None:
        super().__init__()
        self.key = key
        self.value = value
        self.slot = "model" if key is Model else None

    async def setup(self, scope: PluginScope) -> None:
        if self.key is Model and isinstance(self.value, AbstractAsyncContextManager):
            await scope.enter(self.value)
        scope.provide(self.key, self.value)
