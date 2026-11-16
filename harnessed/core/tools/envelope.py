import inspect
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from types import MappingProxyType
from typing import Any, NamedTuple, Self

from pydantic import BaseModel, RootModel

from harnessed.core.messages import ToolCall, to_dict
from harnessed.core.model import ToolDefinition

from .info import ToolEnvelope, ToolInfo

logger = logging.getLogger(__name__)

# Decides from a tool's declared description whether a registry-wide envelope applies.
type EnvelopeSelector = Callable[[ToolInfo], bool]


class InvalidEnvelope(StrEnum):
    """What a call does when the model omits or malforms one envelope's fields.

    `ERROR` fails the call like any other invalid argument, so the tool does not run.
    `DROP` strips the fields, leaves the model out of `ToolEnvelope` and runs the tool.
    """

    ERROR = "error"
    DROP = "drop"


class EnvelopePart(NamedTuple):
    """One registry-wide envelope model selected for a tool."""

    model: type[BaseModel]
    on_invalid: InvalidEnvelope


def envelope_value[T: BaseModel](call: ToolCall, model: type[T]) -> T | None:
    """Read one envelope model from a call's raw arguments without raising.

    Returns None when the call carries none of the model's fields (including calls
    with malformed arguments) or the values do not validate. Event consumers use this to read
    labels from `ToolCallRequestedEvent.call` and later events, which keep the raw
    arguments for replay.
    """
    names = tuple(_envelope_schema(model).properties)
    if not any(name in call.arguments for name in names):
        return None
    try:
        return _validate(model, call.arguments, names)
    except ValueError:
        return None


def envelope_selector(select: EnvelopeSelector | None) -> EnvelopeSelector | None:
    """Validate a registry-wide envelope selector: a synchronous callable or None."""
    if select is not None and (not callable(select) or inspect.iscoroutinefunction(select)):
        raise TypeError("Envelope select must be a synchronous callable receiving ToolInfo")
    return select


def envelope_models(models: Iterable[type[BaseModel]]) -> tuple[type[BaseModel], ...]:
    """Validate an envelope declaration: distinct Pydantic models with object schemas."""
    if isinstance(models, type):
        raise TypeError("envelope must be a tuple of Pydantic models")
    resolved = tuple(models)
    for model in resolved:
        _validate_envelope_model(model)
    if len(set(resolved)) != len(resolved):
        raise ValueError("Tool envelope models must be distinct")
    return resolved


def _validate_envelope_model(model: object) -> None:
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

    Registry-wide envelopes come first, then the tool's own, then its parameters, so
    streamed arguments carry envelope fields before the tool's input. Field names must
    not collide with the tool's parameters or each other.
    """

    definition: ToolDefinition
    models: tuple[type[BaseModel], ...] = ()
    fields: tuple[tuple[str, ...], ...] = ()
    policies: tuple[InvalidEnvelope, ...] = ()

    @classmethod
    def build(
        cls,
        definition: ToolDefinition,
        models: tuple[type[BaseModel], ...],
        *,
        shared: Sequence[EnvelopePart] = (),
        skip_conflicts: bool = False,
    ) -> Self:
        """Merge the tool's own envelopes and the selected registry-wide ones.

        Own envelopes always raise ValueError on a conflict. A conflicting registry-wide
        envelope raises too, unless `skip_conflicts` leaves it out for this tool.
        A registry-wide model the tool already declares itself is not added twice.
        """
        shared = [part for part in _distinct(shared) if part.model not in models]
        if not models and not shared:
            return cls(definition)
        parameters: dict[str, Any] = to_dict(definition.parameters)
        if "$ref" in parameters or parameters.get("type", "object") != "object":
            error = f"Tool {definition.name!r} needs an object parameter schema to accept envelopes"
            if models or not skip_conflicts:
                raise ValueError(error)
            logger.warning("Skipping registry envelopes: %s", error)
            return cls(definition)
        own: dict[str, Any] = parameters.get("properties", {})
        claims = _Claims(definition.name, set(own), parameters.get("$defs", {}))
        declared = [EnvelopePart(model, InvalidEnvelope.ERROR) for model in models]
        for part in declared:
            if (conflict := claims.admit(part.model)) is not None:
                raise ValueError(conflict)
        accepted: list[EnvelopePart] = []
        for part in shared:
            if (conflict := claims.admit(part.model)) is None:
                accepted.append(part)
            elif skip_conflicts:
                logger.warning("Skipping registry envelope: %s", conflict)
            else:
                raise ValueError(conflict)
        parts = (*accepted, *declared)
        if not parts:
            return cls(definition)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for part in parts:
            schema = _envelope_schema(part.model)
            properties.update(schema.properties)
            required.extend(schema.required)
        parameters["type"] = "object"
        parameters["properties"] = properties | own
        parameters["required"] = [*required, *parameters.get("required", ())]
        if not parameters["required"]:
            del parameters["required"]
        if claims.definitions:
            parameters["$defs"] = claims.definitions
        merged = ToolDefinition(definition.name, definition.description, parameters)
        return cls(
            merged,
            tuple(part.model for part in parts),
            tuple(tuple(_envelope_schema(part.model).properties) for part in parts),
            tuple(part.on_invalid for part in parts),
        )

    def split(self, arguments: Mapping[str, object]) -> tuple[ToolEnvelope, Mapping[str, object]]:
        """Validate the envelope fields and return the arguments the tool itself receives.

        Raises ValidationError when an `InvalidEnvelope.ERROR` envelope is missing or
        invalid. `InvalidEnvelope.DROP` envelopes are left out of the result instead.
        Every envelope's fields are removed from the tool's arguments either way.
        """
        if not self.models:
            return ToolEnvelope(), arguments
        values: list[BaseModel] = []
        for model, names, on_invalid in zip(self.models, self.fields, self.policies, strict=True):
            try:
                values.append(_validate(model, arguments, names))
            except ValueError:
                if on_invalid is InvalidEnvelope.ERROR:
                    raise
                logger.debug("Dropped invalid envelope %s", model.__name__, exc_info=True)
        envelope_names = {name for names in self.fields for name in names}
        remaining = {name: value for name, value in arguments.items() if name not in envelope_names}
        return ToolEnvelope(tuple(values)), MappingProxyType(remaining)


class _Claims:
    """Field and `$defs` names already used by one tool's merged schema."""

    def __init__(self, tool: str, names: set[str], definitions: dict[str, Any]) -> None:
        self._tool = tool
        self._parameters = frozenset(names)
        self._names = set(names)
        self.definitions = dict(definitions)

    def admit(self, model: type[BaseModel]) -> str | None:
        """Claim the model's names, or describe the conflict without claiming anything."""
        schema = _envelope_schema(model)
        for name in schema.properties:
            if name in self._parameters:
                return (
                    f"Envelope {model.__name__} field {name!r} conflicts with a "
                    f"parameter of tool {self._tool!r}"
                )
            if name in self._names:
                return (
                    f"Envelope {model.__name__} field {name!r} conflicts with another "
                    f"envelope of tool {self._tool!r}"
                )
        for name, value in schema.definitions.items():
            if name in self.definitions and self.definitions[name] != value:
                return (
                    f"Envelope {model.__name__} schema definition {name!r} conflicts "
                    f"with tool {self._tool!r}"
                )
        self._names.update(schema.properties)
        self.definitions.update(schema.definitions)
        return None


def _distinct(parts: Iterable[EnvelopePart]) -> list[EnvelopePart]:
    seen: set[type[BaseModel]] = set()
    distinct: list[EnvelopePart] = []
    for part in parts:
        if part.model not in seen:
            seen.add(part.model)
            distinct.append(part)
    return distinct


def _validate[T: BaseModel](
    model: type[T], arguments: Mapping[str, object], names: Iterable[str]
) -> T:
    return model.model_validate_json(
        json.dumps(
            to_dict({name: arguments[name] for name in names if name in arguments}),
            allow_nan=False,
        ),
        strict=True,
    )


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
