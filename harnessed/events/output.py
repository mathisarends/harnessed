from dataclasses import field
from typing import Literal
from uuid import uuid4

from pydantic.dataclasses import dataclass

from harnessed.models.values import VALUE_CONFIG, JSONValue

from .base import DurableEvent, Identifier, StepNumber
from .kinds import EventKind


@dataclass(frozen=True, config=VALUE_CONFIG)
class OutputValidatedEvent(DurableEvent):
    """An accepted JSON answer, tied to the exact assistant message it validates."""

    __hash__ = None
    type: Literal[EventKind.OUTPUT_VALIDATED] = field(
        default=EventKind.OUTPUT_VALIDATED, kw_only=True
    )
    turn_id: Identifier
    schema_id: Identifier
    message_id: Identifier
    data: JSONValue
    call_id: str | None = None
    id: Identifier = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True, config=VALUE_CONFIG)
class OutputValidationFailedEvent(DurableEvent):
    """A bounded repair attempt; rejected input values are never echoed here."""

    __hash__ = None
    type: Literal[EventKind.OUTPUT_VALIDATION_FAILED] = field(
        default=EventKind.OUTPUT_VALIDATION_FAILED, kw_only=True
    )
    turn_id: Identifier
    schema_id: Identifier
    message_id: Identifier
    attempt: StepNumber
    message: str
    call_id: str | None = None
