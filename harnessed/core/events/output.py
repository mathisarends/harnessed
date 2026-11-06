from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import PlainSerializer

from harnessed.core.events.base import Identifier, SessionEvent, StepNumber
from harnessed.core.events.kinds import EventKind
from harnessed.core.messages import freeze, to_dict


@dataclass(frozen=True)
class OutputValidatedEvent(SessionEvent):
    """An accepted JSON answer, tied to the exact assistant message it validates."""

    __hash__ = None
    type: Literal[EventKind.OUTPUT_VALIDATED] = field(
        default=EventKind.OUTPUT_VALIDATED, kw_only=True
    )
    turn_id: Identifier
    schema_id: Identifier
    message_id: Identifier
    data: Annotated[Any, PlainSerializer(to_dict, when_used="json")]
    call_id: str | None = None
    id: Identifier = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze(self.data))


@dataclass(frozen=True)
class OutputValidationFailedEvent(SessionEvent):
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
