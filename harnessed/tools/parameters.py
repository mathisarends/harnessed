from dataclasses import dataclass
from typing import Annotated, Any, TypeAliasType, get_args, get_origin

from pydantic import BaseModel

from harnessed.kernel import Inject, InjectMarker

from .enabled import ToolEnabled, validate_enabled


class DisabledToolParameterError(ValueError):
    """A call explicitly supplied a parameter disabled for this activation."""


@dataclass(frozen=True, kw_only=True)
class ToolParameter:
    """Configure a signature parameter's availability for one tool activation.

    ``enabled`` follows whole-tool predicate conventions, but is evaluated once
    after setup. Conditional parameters require a default even when enabled.
    """

    enabled: ToolEnabled = True

    def __post_init__(self) -> None:
        validate_enabled(self.enabled)


def parameter_condition(annotation: Any) -> ToolParameter | None:
    """Read outer Annotated metadata, including reusable type aliases."""
    conditions: list[ToolParameter] = []
    injected = False
    while isinstance(annotation, TypeAliasType) or get_origin(annotation) is Annotated:
        if isinstance(annotation, TypeAliasType):
            annotation = annotation.__value__
        else:
            annotation, *metadata = get_args(annotation)
            conditions.extend(item for item in metadata if isinstance(item, ToolParameter))
            injected = injected or any(isinstance(item, InjectMarker) for item in metadata)
    if len(conditions) > 1:
        raise TypeError("A tool parameter may declare only one ToolParameter")
    if conditions and (injected or get_origin(annotation) is Inject):
        raise TypeError("ToolParameter cannot annotate an injected parameter")
    _reject_nested_conditions(
        annotation,
        set(),
        message="ToolParameter must annotate a signature parameter, not a nested value",
    )
    if conditions:
        return conditions[0]
    return None


def reject_model_conditions(model: type[BaseModel]) -> None:
    """Reject markers in params models instead of dropping model validation semantics."""
    _reject_nested_conditions(
        model,
        set(),
        message="ToolParameter supports signature parameters, not params model fields",
    )


def _reject_nested_conditions(annotation: Any, seen: set[Any], *, message: str) -> None:
    if isinstance(annotation, ToolParameter):
        raise TypeError(message)
    if isinstance(annotation, TypeAliasType):
        if annotation in seen:
            return
        seen.add(annotation)
        _reject_nested_conditions(annotation.__value__, seen, message=message)
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return
        seen.add(annotation)
        for field in annotation.model_fields.values():
            _reject_nested_conditions(field.rebuild_annotation(), seen, message=message)
    else:
        for argument in get_args(annotation):
            _reject_nested_conditions(argument, seen, message=message)
