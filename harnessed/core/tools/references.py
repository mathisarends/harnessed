from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from harnessed.kernel import PluginBase, binding_plan

from .arguments import ToolArguments


@dataclass(frozen=True)
class ToolReference:
    """A decorated declaration, optionally restricted to one plugin recipe."""

    name: str
    declaration: Callable = field(repr=False)
    owner: Any = field(default=None, repr=False)

    def matches(self, callback: Callable) -> bool:
        declaration = getattr(callback, "__func__", callback)
        owner = getattr(callback, "__self__", None)
        return declaration is self.declaration and (
            self.owner is None
            or self.owner is owner
            or isinstance(owner, PluginBase)
            and owner.recipe is self.owner
        )


type ToolSelector = str | Callable[..., Any] | ToolReference


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


def tool_selector(value: ToolSelector) -> str | ToolReference:
    if isinstance(value, str):
        if not value:
            raise ValueError("Tool names must not be empty")
        return value
    return tool_reference(value)


def call_arguments(reference: ToolReference, arguments: dict) -> dict:
    """Validate scripted model arguments, excluding self and injected parameters."""
    plan = binding_plan(reference.declaration)
    parameters = list(plan.signature.parameters.values())
    if parameters and parameters[0].name in ("self", "cls"):
        parameters.pop(0)
    signature = plan.signature.replace(parameters=parameters)
    plan = replace(plan, signature=signature)
    spec = getattr(reference.declaration, "_harnessed_tool", None)
    if spec is None:
        raise TypeError("Tool references must point to a @tool method")
    form = ToolArguments.from_signature(
        reference.name, plan.signature, plan.hints, plan.dependencies, params=spec.params
    )
    parsed = form.parse(arguments)
    # A scripted response carries wire arguments, not the Python parameter object.
    return arguments if spec.params is not None else parsed
