import inspect
import json
from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass, replace
from functools import cached_property
from types import MappingProxyType
from typing import Annotated, Any, Self, TypeAliasType, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model
from pydantic.fields import FieldInfo
from pydantic.json_schema import JsonSchemaValue

from harnessed.kernel import Dependency, PluginScope, RegistrationState, Scope
from harnessed.models.messages import result_content
from harnessed.models.values import to_dict

from .context import ToolContext
from .enabled import bind_enabled
from .overrides import ToolOverride
from .parameters import (
    DisabledToolParameterError,
    ToolParameter,
    parameter_condition,
    reject_model_conditions,
)

_CONFIG = ConfigDict(
    strict=True,
    extra="forbid",
    validate_default=True,
    allow_inf_nan=False,
    hide_input_in_errors=True,
)
_SERIALIZER = TypeAdapter(Any, config=ConfigDict(ser_json_inf_nan="constants"))


@dataclass(frozen=True)
class _ConditionalParameter:
    internal: str
    name: str
    condition: ToolParameter


@dataclass(frozen=True)
class ToolArguments:
    model: type[BaseModel]
    names: Mapping[str, str]
    model_parameter: str | None = None
    conditions: tuple[_ConditionalParameter, ...] = ()
    disabled: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "names", MappingProxyType(dict(self.names)))

    @classmethod
    def from_signature(
        cls,
        name: str,
        signature: inspect.Signature,
        hints: Mapping[str, Any],
        *,
        injected: Mapping[str, Dependency],
        parameter_fields: Mapping[str, FieldInfo] | None = None,
        params: type[BaseModel] | None = None,
    ) -> Self:
        if params is not None and parameter_fields is not None:
            raise ValueError("params cannot be combined with parameter_fields; use model fields")
        _reject_uninjected_context(signature, hints, injected=injected)
        parameter_fields = parameter_fields or {}
        unknown = parameter_fields.keys() - (signature.parameters.keys() - injected.keys())
        if unknown:
            raise ValueError(f"Unknown model parameters in parameter_fields: {sorted(unknown)}")
        parameters = _model_parameters(signature, injected=injected)
        if params is not None:
            return cls._from_params(params, parameters, hints)
        # Pydantic accepts runtime type expressions as field definitions.
        fields: dict[str, Any] = {}
        names: dict[str, str] = {}
        conditions: list[_ConditionalParameter] = []
        for parameter in parameters:
            # Aliases preserve Python parameter names, including BaseModel's reserved names.
            internal = f"argument_{len(fields)}"
            fields[internal] = _field_definition(parameter, hints, parameter_fields)
            names[internal] = parameter.name
            condition = parameter_condition(hints.get(parameter.name, parameter.annotation))
            if condition is not None:
                if parameter.default is inspect.Parameter.empty:
                    raise TypeError(f"Conditional parameter {parameter.name!r} requires a default")
                conditions.append(_ConditionalParameter(internal, parameter.name, condition))
        model = create_model(f"{name}Arguments", __config__=_CONFIG, **fields)
        model.model_json_schema()  # Reject annotations without a JSON schema at registration.
        for conditional in conditions:
            if model.model_fields[conditional.internal].is_required():
                raise TypeError(f"Conditional parameter {conditional.name!r} requires a default")
        return cls(model, names, conditions=tuple(conditions))

    def configured(self, settings: ToolOverride, *, tool_name: str) -> Self:
        if not settings.parameters:
            return self
        model = settings.derive(self.model, self.schema, tool_name=tool_name)
        return replace(self, model=model)

    def activate(
        self,
        callback: Callable[..., object],
        owner: RegistrationState,
        *,
        scope: Scope | PluginScope,
    ) -> Self:
        """Freeze parameter availability against this registration's mounted scope."""
        disabled: set[str] = set()
        for parameter in self.conditions:
            check = bind_enabled(parameter.condition.enabled, callback, owner)
            if not check(scope):
                disabled.add(parameter.name)
        return replace(self, disabled=frozenset(disabled))

    @cached_property
    def schema(self) -> JsonSchemaValue:
        schema = self.model.model_json_schema(by_alias=True)
        if self.disabled:
            properties = schema.get("properties", {})
            for name in self.disabled:
                properties.pop(name, None)
            required = [name for name in schema.get("required", ()) if name not in self.disabled]
            if required:
                schema["required"] = required
            else:
                schema.pop("required", None)
        if self.model_parameter is not None:
            # Recursive models put their root object in $defs instead of at the root.
            if "$ref" in schema:
                root = schema["$defs"][schema.pop("$ref").removeprefix("#/$defs/")]
                schema.update(root)
            if schema.get("type") != "object":
                raise TypeError("params must define an object JSON schema")
            schema["additionalProperties"] = False
        return schema

    def parse(self, arguments: Mapping[str, object]) -> dict[str, object]:
        supplied = self.disabled.intersection(arguments)
        if supplied:
            raise DisabledToolParameterError(
                f"Disabled tool parameters cannot be supplied: {sorted(supplied)}"
            )
        parsed = self.model.model_validate_json(
            json.dumps(to_dict(arguments), allow_nan=False),
            strict=True,
            extra="forbid" if self.model_parameter is not None else None,
        )
        if self.model_parameter is not None:
            return {self.model_parameter: parsed}
        return {name: getattr(parsed, internal) for internal, name in self.names.items()}

    @classmethod
    def _from_params(
        cls,
        params: type[BaseModel],
        parameters: list[inspect.Parameter],
        hints: Mapping[str, Any],
    ) -> Self:
        if len(parameters) != 1:
            raise TypeError("params requires exactly one non-injected tool parameter")
        parameter = parameters[0]
        reject_model_conditions(params)
        if parameter_condition(hints.get(parameter.name, parameter.annotation)) is not None:
            raise TypeError("ToolParameter supports signature parameters, not params models")
        if _unwrap(hints.get(parameter.name, parameter.annotation)) is not params:
            raise TypeError(f"Tool parameter {parameter.name!r} must be annotated with params")
        if parameter.default is not inspect.Parameter.empty:
            raise TypeError("The params parameter must not have a default; use model fields")
        arguments = cls(params, {}, parameter.name)
        _ = arguments.schema  # Reject models without an object JSON schema at registration.
        return arguments


def tool_result_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return result_content(_SERIALIZER.dump_python(value, mode="json"))


def _unwrap(annotation: Any) -> Any:
    """Strip type aliases and Annotated metadata down to the underlying type expression."""
    while isinstance(annotation, TypeAliasType) or get_origin(annotation) is Annotated:
        if isinstance(annotation, TypeAliasType):
            annotation = annotation.__value__
        else:
            annotation = get_args(annotation)[0]
    return annotation


def _reject_uninjected_context(
    signature: inspect.Signature, hints: Mapping[str, Any], *, injected: Mapping[str, Dependency]
) -> None:
    for parameter in signature.parameters.values():
        original = hints.get(parameter.name, parameter.annotation)
        if parameter.name in injected and parameter_condition(original) is not None:
            raise TypeError("ToolParameter cannot annotate an injected parameter")
        annotation = _unwrap(original)
        if annotation is ToolContext and parameter.name not in injected:
            raise TypeError(
                f"Tool parameter {parameter.name!r} must use Inject[ToolContext] "
                "to mark framework-provided arguments"
            )


def _model_parameters(
    signature: inspect.Signature, *, injected: Mapping[str, Dependency]
) -> list[inspect.Parameter]:
    """The named parameters the model supplies, excluding framework injections."""
    parameters: list[inspect.Parameter] = []
    for parameter in signature.parameters.values():
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise TypeError(
                "Tools must have named parameters (no *args, **kwargs or positional-only)"
            )
        if parameter.name not in injected:
            parameters.append(parameter)
    return parameters


def _field_definition(
    parameter: inspect.Parameter,
    hints: Mapping[str, Any],
    parameter_fields: Mapping[str, FieldInfo],
) -> tuple[Any, FieldInfo]:
    """Build the Pydantic annotation and aliased field for one model parameter."""
    annotation = hints.get(parameter.name, parameter.annotation)
    if annotation is inspect.Parameter.empty:
        raise TypeError(f"Tool parameter {parameter.name} requires a type annotation")
    if get_origin(annotation) is Annotated:
        base, *metadata = get_args(annotation)
        descriptions = [item for item in metadata if isinstance(item, str)]
        if descriptions:
            annotation = Annotated[base, *metadata, Field(description=descriptions[0])]
    if parameter.name in parameter_fields:
        annotation = Annotated[annotation, parameter_fields[parameter.name]]
    default = parameter.default
    if isinstance(default, FieldInfo):
        field = copy(default)
        field.alias = field.validation_alias = field.serialization_alias = parameter.name
    else:
        field = Field(
            default=... if default is inspect.Parameter.empty else default,
            alias=parameter.name,
        )
    return annotation, field
