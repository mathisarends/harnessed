from collections.abc import Mapping
from dataclasses import field
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from harnessed.core.assets import AssetValue
from harnessed.core.errors import SessionFormatError
from harnessed.core.events.approvals import ApprovalRequestedEvent, ApprovalResolvedEvent
from harnessed.core.events.base import DurableEvent
from harnessed.core.events.inbox import InboxAddedEvent
from harnessed.core.events.registry import EventRegistry
from harnessed.core.events.session import AgentControlChangedEvent
from harnessed.core.events.turn import TurnEndedEvent, TurnStartedEvent
from harnessed.core.events.unions import SESSION_EVENT_TYPES, LogEvent
from harnessed.core.messages import JSONMapping, freeze, to_dict

from .codec import CURRENT_VERSION, _validate_event
from .files import TextValue
from .state import SessionState

_CONFIG = ConfigDict(
    extra="forbid",
    strict=True,
    allow_inf_nan=False,
    ser_json_bytes="base64",
    val_json_bytes="base64",
    hide_input_in_errors=True,
)


@dataclass(frozen=True, config=_CONFIG)
class CheckpointExtension:
    """Lossless application-event data, independent of a store's plugin catalog.

    Only the owning session decodes this data against its registered event class.
    Stores can round-trip it without importing application plugins. The payload
    is the explicit dynamic boundary; built-in checkpoint events remain typed.
    """

    __hash__ = None

    tag: str
    data: JSONMapping
    type: Literal["checkpoint.extension"] = field(default="checkpoint.extension", kw_only=True)

    def __post_init__(self) -> None:
        if (
            "." not in self.tag
            or any(not part for part in self.tag.split("."))
            or self.tag in SESSION_EVENT_TYPES
        ):
            raise ValueError("Checkpoint extensions require an application event tag")
        if not isinstance(self.data, Mapping):
            raise TypeError("Checkpoint extension data must be an object")
        object.__setattr__(self, "data", freeze(self.data))

    @classmethod
    def from_event(cls, event: DurableEvent, *, registry: EventRegistry) -> Self:
        """Capture an event after validation against the session's catalog."""
        return cls(tag=event.type, data=registry.encode(event)["data"])

    def to_event(self, *, registry: EventRegistry) -> DurableEvent:
        """Validate and restore an explicitly registered application event."""
        event = registry.decode(
            {"version": CURRENT_VERSION, "type": self.tag, "data": to_dict(self.data)}
        )
        if not isinstance(event, DurableEvent):
            raise SessionFormatError("Checkpoint extensions require a durable application event")
        return event


type CheckpointEntry = Annotated[LogEvent | CheckpointExtension, Field(discriminator="type")]
type CheckpointSeed = Annotated[
    InboxAddedEvent | ApprovalRequestedEvent | ApprovalResolvedEvent, Field(discriminator="type")
]


@dataclass(frozen=True, config=_CONFIG)
class SessionCheckpoint:
    """An immutable conversation boundary and its current bounded execution suffix.

    This is an SDK exchange value, not an application's database schema. Stores
    may map every field into their own records, but must reconstruct the complete
    checkpoint. Serialization performs no I/O and requires no plugin catalog.
    Application event schemas are checked by the owning session before replay.
    """

    __hash__ = None

    state: SessionState
    offset: Annotated[int, Field(ge=0)]
    control: AgentControlChangedEvent
    seed: tuple[CheckpointSeed, ...] = ()
    events: tuple[CheckpointEntry, ...] = ()
    assets: tuple[AssetValue, ...] = ()
    files: tuple[TextValue, ...] = ()
    version: Literal[2] = 2

    def __post_init__(self) -> None:
        if type(self.version) is not int:
            raise ValueError("Checkpoint versions must be integers")
        if self.events and (
            not isinstance(self.events[0], TurnStartedEvent)
            or any(
                isinstance(event, TurnStartedEvent | TurnEndedEvent) for event in self.events[1:]
            )
        ):
            raise ValueError("A checkpoint suffix must describe one open turn")
        for event in (*self.seed, *self.events, self.control):
            if not isinstance(event, CheckpointExtension):
                _validate_event(event)

    def to_json(self) -> str:
        """Serialize the canonical versioned document in memory, without I/O."""
        return _CHECKPOINT.dump_json(self).decode("utf-8")

    @classmethod
    def from_json(cls, payload: str | bytes) -> Self:
        """Validate structural data without needing application event classes."""
        try:
            return TypeAdapter(cls).validate_json(payload)
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise SessionFormatError("Invalid session checkpoint") from error


_CHECKPOINT = TypeAdapter(SessionCheckpoint)

__all__ = ["CheckpointEntry", "CheckpointExtension", "CheckpointSeed", "SessionCheckpoint"]
