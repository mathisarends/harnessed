import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from functools import cache, reduce
from operator import or_
from types import MappingProxyType
from typing import Annotated, Any, Protocol, cast, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    TypeAdapter,
    ValidationError,
)

from harnessed.errors import SessionFormatError
from harnessed.events import DurableEvent, TransientEvent
from harnessed.models.values import VALUE_CONFIG, to_dict

from .unions import SESSION_EVENT_TYPES

CURRENT_VERSION = 9


class SessionRecord(BaseModel):
    """The persisted envelope of one durable event; the payload is checked by its catalog."""

    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )

    version: int
    seq: NonNegativeInt | None = None
    type: str
    data: dict[str, JsonValue]


@runtime_checkable
class EventDecoder(Protocol):
    """Decodes one persisted session record into its durable event."""

    def decode(self, record: Mapping[str, Any]) -> DurableEvent:
        """Validate ``record`` and return its frozen event."""
        ...


class SessionCodec:
    """The current session record format for one catalog of durable events."""

    def __init__(self, kinds: Mapping[str, type[DurableEvent]]) -> None:
        self._kinds = MappingProxyType(dict(kinds))
        annotation = cast(Any, Annotated)[reduce(or_, kinds.values()), Field(discriminator="type")]
        self.adapter: TypeAdapter[DurableEvent] = TypeAdapter(annotation, config=VALUE_CONFIG)

    def encode(self, event: DurableEvent) -> dict[str, Any]:
        """Encode a canonical dotted tag and the current version without mutable state."""
        if not isinstance(event, DurableEvent):
            raise TypeError("Only durable events can be persisted")
        validate_event(event, self._kinds)
        data = to_dict(event)
        data.pop("type")
        return {"version": CURRENT_VERSION, "type": event.type, "data": data}

    def decode(self, record: Mapping[str, Any]) -> DurableEvent:
        """Validate the canonical envelope and frozen discriminated event payload."""
        try:
            envelope = SessionRecord.model_validate(record)
        except ValidationError as error:
            raise SessionFormatError("Invalid session record envelope") from error
        if envelope.version != CURRENT_VERSION:
            raise SessionFormatError("Unsupported session record version")
        if envelope.type not in self._kinds:
            raise SessionFormatError(f"Unknown session event type: {envelope.type}")
        if envelope.data.get("type", envelope.type) != envelope.type:
            raise SessionFormatError("Session envelope and payload type tags disagree")
        # JSON-mode validation accepts enum values and arrays exactly as persisted.
        payload = json.dumps({**envelope.data, "type": envelope.type}, allow_nan=False)
        try:
            event = self.adapter.validate_json(payload, strict=True)
        except ValidationError as error:
            raise SessionFormatError(f"Invalid {envelope.type} record payload") from error
        validate_event(event, self._kinds)
        return event


@cache
def builtin_codec() -> SessionCodec:
    """Return the shared codec of built-in durable events."""
    return SessionCodec(SESSION_EVENT_TYPES)


def validate_event(
    event: DurableEvent | TransientEvent,
    kinds: Mapping[str, type] = SESSION_EVENT_TYPES,
) -> None:
    """Require a registered event class and deeply immutable, finite JSON values.

    Built-in events already validate their fields; this check also covers
    application-defined events declared as plain frozen dataclasses.
    """
    if kinds.get(event.type) is not type(event):
        raise TypeError("Only registered frozen DurableEvent instances may be appended")
    _require_immutable(event)


def _require_immutable(value: object) -> None:
    match value:
        case None | str() | bool() | int():
            return
        case float() if math.isfinite(value):
            return
        case tuple():
            for item in value:
                _require_immutable(item)
        case MappingProxyType():
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("Session event mapping keys must be strings")
                _require_immutable(item)
        case _ if (
            is_dataclass(value)
            and not isinstance(value, type)
            and cast(Any, value).__dataclass_params__.frozen
        ):
            for item in fields(value):
                _require_immutable(getattr(value, item.name))
        case _:
            raise TypeError("Session events must contain only immutable, finite values")
