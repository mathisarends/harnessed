from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from types import get_original_bases
from typing import Any, ClassVar, Self, TypeVar, cast, get_args, get_origin, overload

from pydantic import BaseModel, ConfigDict

from harnessed.core.prompt.values import (
    InstructionProvider,
    InstructionRef,
    Instructions,
    PromptContribution,
)
from harnessed.core.session.events import SessionEvent
from harnessed.core.stream import TransientEvent
from harnessed.core.tools import ToolSpec
from harnessed.core.tools.decorator import _activation_tool
from harnessed.kernel import Bundle as KernelBundle
from harnessed.kernel import (
    Context,
    HookEntry,
    Key,
    PluginLike,
    Registration,
    ServiceKey,
    as_plugin_spec,
    hook,
)
from harnessed.kernel import PluginBase as KernelPlugin

from .model import Model


class Bundle(KernelBundle):
    """A collection of kernel hooks and registration recipes."""


class PluginConfig(BaseModel):
    """Default external configuration policy; plugin schemas may extend it."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class Plugin[ConfigT: BaseModel](KernelPlugin):
    """A configured recipe reconstructed for each activation.

    Declare lifetime service bindings with dependency() and mutable runtime data
    with PluginState. Constructors describe capabilities; setup acquires resources
    and registers cleanup on its Context. Instructions require explicit placement.
    """

    config: ConfigT
    events: tuple[type[SessionEvent | TransientEvent], ...] = ()
    _configuration_type: ClassVar[type[BaseModel] | None] = None

    @overload
    def __init__(
        self: Plugin[PluginConfig],
        *,
        config: None = None,
        name: str | None = None,
        hooks: Iterable[HookEntry] = (),
        needs: Iterable[ServiceKey[Any]] | None = None,
        registrations: Iterable[tuple[Registration, Callable]] = (),
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        config: ConfigT,
        name: str | None = None,
        hooks: Iterable[HookEntry] = (),
        needs: Iterable[ServiceKey[Any]] | None = None,
        registrations: Iterable[tuple[Registration, Callable]] = (),
    ) -> None: ...

    def __init__(
        self,
        *,
        config: ConfigT | None = None,
        name: str | None = None,
        hooks: Iterable[HookEntry] = (),
        needs: Iterable[ServiceKey[Any]] | None = None,
        registrations: Iterable[tuple[Registration, Callable]] = (),
    ) -> None:
        super().__init__(name=name, hooks=hooks, needs=needs, registrations=registrations)
        schema = type(self)._configuration_type
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

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "Config" in vars(cls):
            raise TypeError("Config aliases were removed; declare Plugin[YourConfig] instead")
        cls._configuration_type = cls._configuration_schema()
        for name, member in tuple(vars(cls).items()):
            if callable(member) and isinstance(getattr(member, "_harnessed_tool", None), ToolSpec):
                setattr(cls, name, _activation_tool(member))

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "config" and "config" in vars(self):
            raise AttributeError("Plugin configuration cannot be replaced after construction")
        super().__setattr__(name, value)

    def create_instance(self) -> Self:
        """Reconstruct runtime attributes while retaining the validated frozen configuration."""
        if "config" not in vars(self):
            raise TypeError("Plugin constructors must call super().__init__()")
        instance = super().create_instance()
        object.__setattr__(instance, "config", self.config)
        return instance

    def instruction_refs(self) -> tuple[InstructionRef, ...]:
        """Declared system sections, bound to this recipe without evaluating providers."""
        members: dict[str, object] = {}
        for base in reversed(type(self).__mro__):
            members.update(vars(base))
        members.update(vars(self))
        sections: list[InstructionRef] = []
        for name, member in members.items():
            if isinstance(member, Instructions):
                sections.append(member.bind(self, name))
            elif isinstance(member, InstructionProvider):
                sections.append(member.__get__(self, type(self)))
        return tuple(sections)

    @property
    def context_description(self) -> tuple[str, str] | None:
        """A named context value this plugin maintains, listed in the system prompt."""
        return None

    @classmethod
    def _configuration_schema(cls) -> type[BaseModel] | None:
        """Resolve model parameters through generic bases without evaluating annotations."""

        def resolve(value: object, substitutions: dict[TypeVar, object]) -> object:
            while isinstance(value, TypeVar) and value in substitutions:
                replacement = substitutions[value]
                if replacement is value:
                    break
                value = replacement
            if isinstance(value, TypeVar):
                return value.__bound__
            return value

        def visit(base: object, substitutions: dict[TypeVar, object]) -> list[type[BaseModel]]:
            origin = get_origin(base) or base
            if not isinstance(origin, type) or not issubclass(origin, KernelPlugin):
                return []
            arguments = get_args(base)
            if origin is Plugin:
                if not arguments:
                    return []
                model = resolve(arguments[0], substitutions)
                if model is BaseModel:
                    return []
                if not isinstance(model, type) or not issubclass(model, BaseModel):
                    raise TypeError("Plugin[...] requires a Pydantic configuration model")
                return [model]
            bindings = dict(substitutions)
            parameters = vars(origin).get("__parameters__", ())
            for parameter, argument in zip(parameters, arguments, strict=bool(arguments)):
                bindings[parameter] = resolve(argument, substitutions)
            return [
                model for parent in get_original_bases(origin) for model in visit(parent, bindings)
            ]

        models = tuple(dict.fromkeys(visit(cls, {})))
        if not models:
            return None
        selected = next(
            (model for model in models if all(issubclass(model, other) for other in models)), None
        )
        if selected is None:
            raise TypeError("Plugin bases declare incompatible configuration models")
        return selected


def plugin_events(value: PluginLike) -> tuple[type[SessionEvent | TransientEvent], ...]:
    """Event types a plugin declares; only core Plugin recipes declare any."""
    return value.events if isinstance(value, Plugin) else ()


def plugin_contribution(value: PluginLike) -> PromptContribution:
    """Normalize a plugin's prompt metadata once at the agent boundary."""
    spec = as_plugin_spec(value)
    sections = value.instruction_refs() if isinstance(value, Plugin) else ()
    description = value.context_description if isinstance(value, Plugin) else None
    if description is not None and (
        not isinstance(description, tuple)
        or len(description) != 2
        or not all(isinstance(part, str) for part in description)
    ):
        raise TypeError("Context descriptions must contain a name and text")
    return PromptContribution(
        spec.name, sections, description, value if isinstance(value, Plugin) else None
    )


class Service[T](Plugin):
    """Provide a typed instance as a normal, reversible plugin."""

    def __init__(self, key: type[T] | Key[T], value: T) -> None:
        super().__init__()
        self.key = key
        self.value = value
        self.slot = "model" if key is Model else None

    async def setup(self, ctx: Context) -> None:
        if self.key is Model and isinstance(self.value, AbstractAsyncContextManager):
            await ctx.enter(self.value)
        ctx.provide(self.key, self.value)


__all__ = [
    "Bundle",
    "Plugin",
    "PluginConfig",
    "PluginLike",
    "Service",
    "hook",
    "plugin_contribution",
    "plugin_events",
]
