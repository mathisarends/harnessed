import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import Any, Self

from pydantic import BaseModel, RootModel

from harnessed.core.messages import to_dict
from harnessed.core.model import ToolDefinition

from .info import ToolEnvelope, ToolInfo

# Selectors see each tool's own description, before any envelope is merged in.
type EnvelopeSelector = Callable[[ToolInfo], bool]


def envelope_models(models: Iterable[type[BaseModel]]) -> tuple[type[BaseModel], ...]:
    """Validate an envelope declaration: distinct Pydantic models with object schemas."""
    if isinstance(models, type):
        raise TypeError("envelope must be a tuple of Pydantic models")
    resolved = tuple(models)
    for model in resolved:
        validate_envelope_model(model)
    if len(set(resolved)) != len(resolved):
        raise ValueError("Tool envelope models must be distinct")
    return resolved


def validate_envelope_model(model: object) -> None:
    if (
        not isinstance(model, type)
        or not issubclass(model, BaseModel)
        or model is BaseModel
        or issubclass(model, RootModel)
    ):
        raise TypeError("Tool envelopes must be Pydantic BaseModel subclasses, not RootModel")
    _ = _envelope_schema(model)  # Reject models without object properties at declaration.


@dataclass(frozen=True)
class EnvelopeLayout:
    """One tool's model-facing definition with the envelope fields of its call merged in.

    Envelope properties come first so streamed arguments carry them before the tool's
    own input. Field names must not collide with the tool's parameters or each other.
    """

    definition: ToolDefinition
    models: tuple[type[BaseModel], ...] = ()
    fields: tuple[tuple[str, ...], ...] = ()

    @classmethod
    def build(cls, definition: ToolDefinition, models: tuple[type[BaseModel], ...]) -> Self:
        if not models:
            return cls(definition)
        parameters: dict[str, Any] = to_dict(definition.parameters)
        if "$ref" in parameters or parameters.get("type", "object") != "object":
            raise ValueError(
                f"Tool {definition.name!r} needs an object parameter schema to accept envelopes"
            )
        own: dict[str, Any] = parameters.get("properties", {})
        properties: dict[str, Any] = {}
        required: list[str] = []
        definitions: dict[str, Any] = parameters.get("$defs", {})
        fields: list[tuple[str, ...]] = []
        for model in models:
            schema = _envelope_schema(model)
            for name in schema.properties:
                if name in own:
                    raise ValueError(
                        f"Envelope {model.__name__} field {name!r} conflicts with a "
                        f"parameter of tool {definition.name!r}"
                    )
                if name in properties:
                    raise ValueError(
                        f"Envelope {model.__name__} field {name!r} conflicts with another "
                        f"envelope of tool {definition.name!r}"
                    )
            for name, value in schema.definitions.items():
                if name in definitions and definitions[name] != value:
                    raise ValueError(
                        f"Envelope {model.__name__} schema definition {name!r} conflicts "
                        f"with tool {definition.name!r}"
                    )
                definitions[name] = value
            properties.update(schema.properties)
            required.extend(schema.required)
            fields.append(tuple(schema.properties))
        parameters["type"] = "object"
        parameters["properties"] = properties | own
        parameters["required"] = [*required, *parameters.get("required", ())]
        if not parameters["required"]:
            del parameters["required"]
        if definitions:
            parameters["$defs"] = definitions
        merged = ToolDefinition(definition.name, definition.description, parameters)
        return cls(merged, models, tuple(fields))

    def split(self, arguments: Mapping[str, object]) -> tuple[ToolEnvelope, Mapping[str, object]]:
        """Validate the envelope fields and return the arguments the tool itself receives.

        Raises ValidationError when an envelope field is missing or invalid.
        """
        if not self.models:
            return ToolEnvelope(), arguments
        values = tuple(
            model.model_validate_json(
                json.dumps(
                    to_dict({name: arguments[name] for name in names if name in arguments}),
                    allow_nan=False,
                ),
                strict=True,
            )
            for model, names in zip(self.models, self.fields, strict=True)
        )
        envelope_names = {name for names in self.fields for name in names}
        remaining = {name: value for name, value in arguments.items() if name not in envelope_names}
        return ToolEnvelope(values), MappingProxyType(remaining)


@dataclass(frozen=True)
class _EnvelopeSchema:
    properties: dict[str, Any]
    required: tuple[str, ...]
    definitions: dict[str, Any]


@cache
def _envelope_schema(model: type[BaseModel]) -> _EnvelopeSchema:
    schema = model.model_json_schema(by_alias=True)
    definitions: dict[str, Any] = schema.pop("$defs", {})
    if "$ref" in schema:
        # Recursive models put their root object in $defs instead of at the root.
        schema = dict(definitions[schema["$ref"].removeprefix("#/$defs/")])
    properties: dict[str, Any] = schema.get("properties", {})
    if schema.get("type") != "object" or not properties:
        raise TypeError(f"Envelope {model.__name__} must define object fields")
    return _EnvelopeSchema(properties, tuple(schema.get("required", ())), definitions)
