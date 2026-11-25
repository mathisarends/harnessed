from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from types import MethodType
from typing import Any

from harnessed.kernel import PluginBase, binding_plan

from .arguments import ToolArguments
from .overrides import callback_override


@dataclass(frozen=True)
class ToolReference:
    """A decorated declaration, optionally restricted to one plugin recipe."""

    name: str
    declaration: Callable = field(repr=False)
    owner: Any = field(default=None, repr=False)

    def matches(self, callback: Callable) -> bool:
        declaration = getattr(callback, "__func__", callback)
        owner = getattr(callback, "__self__", None)
        return self.matches_binding(declaration, owner)

    def matches_binding(self, declaration: Callable, owner: object | None) -> bool:
        """Match a registered or excluded declaration by identity and optional owner."""
        return declaration is self.declaration and (
            self.owner is None
            or self.owner is owner
            or (isinstance(owner, PluginBase) and owner.recipe is self.owner)
        )


# Selectors never accept plain names: an action member matches every tool that
# declares it, and a @tool method matches its registrations, narrowed to one
# plugin recipe when the method is bound to that recipe.
type ToolSelector = StrEnum | Callable[..., Any] | ToolReference


def tool_reference(value: Callable | ToolReference) -> ToolReference:
    if isinstance(value, ToolReference):
        return value
    declaration = getattr(value, "__func__", value)
    spec = getattr(declaration, "_harnessed_tool", None)
    if spec is None:
        raise TypeError("Tool references must point to a @tool method")
    return ToolReference(
        spec.name or getattr(declaration, "__name__", ""),
        declaration,
        getattr(value, "__self__", None),
    )


def tool_selector(value: ToolSelector) -> StrEnum | ToolReference:
    """Normalize a selector, rejecting plain tool names."""
    if isinstance(value, StrEnum):
        return value
    if isinstance(value, str):
        raise TypeError("Tool selectors require an action enum member or @tool method")
    return tool_reference(value)


def tool_name(value: Callable | ToolReference) -> str:
    """Return the model-facing name of a decorated tool, for prompts and static text."""
    return tool_reference(value).name


def declared_arguments(reference: ToolReference) -> ToolArguments:
    """Build the static argument model before activation-specific parameter fields."""
    plan = binding_plan(reference.declaration)
    parameters = list(plan.signature.parameters.values())
    if parameters and parameters[0].name in ("self", "cls"):
        parameters.pop(0)
    signature = plan.signature.replace(parameters=parameters)

    spec = getattr(reference.declaration, "_harnessed_tool", None)
    if spec is None:
        raise TypeError("Tool references must point to a @tool method")
    return ToolArguments.from_signature(
        reference.name,
        signature,
        plan.hints,
        injected=plan.dependencies,
        params=spec.params,
    )


def call_arguments(reference: ToolReference, arguments: dict) -> dict:
    """Validate scripted arguments and leave omitted defaults to the mounted recipe."""
    form = declared_arguments(reference)
    if reference.owner is not None:
        callback = MethodType(reference.declaration, reference.owner)
        form = form.configured(callback_override(callback), tool_name=reference.name)
    form.parse(arguments)
    return arguments
