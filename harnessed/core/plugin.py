from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from typing import Any, ClassVar, Self

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


class Plugin(KernelPlugin):
    """A configured recipe reconstructed for each activation.

    Declare lifetime service bindings with dependency() and mutable runtime data
    with PluginState. Constructors describe capabilities; setup acquires resources
    and registers cleanup on its Context. Instructions require explicit placement.
    """

    Config: ClassVar[type[BaseModel]] = PluginConfig
    config: Any
    events: tuple[type[SessionEvent | TransientEvent], ...] = ()

    def __init__(
        self,
        *,
        name: str | None = None,
        hooks: Iterable[HookEntry] = (),
        needs: Iterable[ServiceKey[Any]] | None = None,
        registrations: Iterable[tuple[Registration, Callable]] = (),
        **configuration: Any,
    ) -> None:
        super().__init__(name=name, hooks=hooks, needs=needs, registrations=registrations)
        if not type(self).Config.model_config.get("frozen"):
            raise TypeError("Plugin Config models must be frozen; inherit PluginConfig")
        self.config = type(self).Config.model_validate(configuration)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
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
