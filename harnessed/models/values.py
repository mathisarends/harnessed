import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import ConfigDict, Field, PlainSerializer, PlainValidator

# Shared by every validated value object: no coercion, no unknown fields and
# no non-finite numbers. Decoding applies the same rules to JSON input.
# Defaults are validated too, so default JSON containers are frozen as well.
VALUE_CONFIG = ConfigDict(
    strict=True,
    extra="forbid",
    allow_inf_nan=False,
    hide_input_in_errors=True,
    validate_default=True,
)


def freeze(value: Any) -> Any:
    """Return a lossless JSON value as nested tuples and read-only mappings."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        frozen = {key: freeze(item) for key, item in value.items()}
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(freeze(item) for item in value)
    raise TypeError(f"Not a lossless JSON value: {type(value).__name__}")


def to_dict(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_dict(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: to_dict(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_dict(item) for item in value]
    return value


def unique[T](values: tuple[T, ...]) -> tuple[T, ...]:
    """Return values unchanged, or raise ValueError when any value repeats."""
    if len(set(values)) != len(values):
        raise ValueError("Values must be unique")
    return values


def _frozen_json(value: object) -> Any:
    try:
        return freeze(value)
    except TypeError as error:
        # Pydantic reports ValueError as a validation failure; TypeError would escape.
        raise ValueError(str(error)) from error


def _frozen_json_object(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Expected a JSON object")
    return _frozen_json(value)


# A JSON object, frozen into tuples and read-only mappings on validation.
type JSONMapping = Annotated[
    Mapping[str, Any],
    PlainValidator(_frozen_json_object, json_schema_input_type=dict[str, Any]),
    PlainSerializer(to_dict, return_type=dict[str, Any], when_used="json"),
]
# Any JSON value, frozen into tuples and read-only mappings on validation.
type JSONValue = Annotated[
    Any,
    PlainValidator(_frozen_json, json_schema_input_type=Any),
    PlainSerializer(to_dict, when_used="json"),
]
type NonEmptyText = Annotated[str, Field(min_length=1)]
