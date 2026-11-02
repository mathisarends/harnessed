from dataclasses import dataclass
from typing import Any, Self

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass as validated_dataclass

from harnessed.core.assets import AssetValue
from harnessed.core.errors import SessionFormatError
from harnessed.core.event_registry import EventRegistry

from .events import (
    AgentControlChangedEvent,
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    InboxAddedEvent,
    SessionEvent,
    TurnEndedEvent,
    TurnStartedEvent,
)
from .files import TextValue
from .state import SessionState


@dataclass(frozen=True)
class SessionCheckpoint:
    """A conversation boundary plus the current execution's bounded replay suffix."""

    state: SessionState
    offset: int
    seed: tuple[SessionEvent, ...]
    events: tuple[SessionEvent, ...]
    control: AgentControlChangedEvent
    assets: tuple[AssetValue, ...]
    files: tuple[TextValue, ...]

    def to_json(self, *, registry: EventRegistry) -> str:
        record = _CheckpointRecord(
            state=self.state,
            offset=self.offset,
            seed=tuple(registry.encode(event) for event in self.seed),
            events=tuple(registry.encode(event) for event in self.events),
            control=registry.encode(self.control),
            assets=self.assets,
            files=self.files,
        )
        return _CHECKPOINT.dump_json(record).decode("utf-8")

    @classmethod
    def from_json(cls, payload: str, *, registry: EventRegistry) -> Self:
        try:
            record = _CHECKPOINT.validate_json(payload)
            if type(record.version) is not int:
                raise ValueError("Checkpoint versions must be integers")
            seed = tuple(registry.decode(item) for item in record.seed)
            if any(
                not isinstance(
                    event, InboxAddedEvent | ApprovalRequestedEvent | ApprovalResolvedEvent
                )
                for event in seed
            ):
                raise ValueError("Invalid checkpoint seed")
            control = registry.decode(record.control)
            if not isinstance(control, AgentControlChangedEvent):
                raise ValueError("Invalid checkpoint control")
            events = tuple(registry.decode(item) for item in record.events)
            if events and (
                not isinstance(events[0], TurnStartedEvent)
                or any(isinstance(event, TurnStartedEvent | TurnEndedEvent) for event in events[1:])
            ):
                raise ValueError("A checkpoint suffix must describe one open turn")
            return cls(
                record.state,
                record.offset,
                seed,
                events,
                control,
                record.assets,
                record.files,
            )
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise SessionFormatError("Invalid session checkpoint") from error


@validated_dataclass(
    frozen=True,
    config=ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        ser_json_bytes="base64",
        val_json_bytes="base64",
        hide_input_in_errors=True,
    ),
)
class _CheckpointRecord:
    # Event payloads cross the heterogeneous, application-extended event catalog here.
    state: SessionState
    offset: int = Field(ge=0)
    seed: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    control: dict[str, Any] = Field(default_factory=dict)
    assets: tuple[AssetValue, ...] = ()
    files: tuple[TextValue, ...] = ()
    version: int = Field(default=1, ge=1, le=1)


_CHECKPOINT = TypeAdapter(_CheckpointRecord)
