from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from harnessed.errors import SessionFormatError
from harnessed.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    DurableEvent,
    InboxAddedEvent,
    LogEvent,
    ModelRequestedEvent,
    SessionControlChangedEvent,
    TurnEndedEvent,
    TurnStartedEvent,
)
from harnessed.events.codec import CURRENT_VERSION, validate_event
from harnessed.events.unions import SESSION_EVENT_TYPES
from harnessed.models import Message
from harnessed.models.assets import AssetValue
from harnessed.models.values import VALUE_CONFIG, JSONMapping, JSONValue, to_dict

from .files import TextValue
from .state import SessionState

if TYPE_CHECKING:
    from harnessed.events.registry import EventRegistry

_CONFIG = ConfigDict(**VALUE_CONFIG, ser_json_bytes="base64", val_json_bytes="base64")


class CheckpointRecordKind(StrEnum):
    """Checkpoint-only record tags that sit beside built-in log events."""

    EXTENSION = "checkpoint.extension"
    REQUEST = "checkpoint.request"


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
    type: Literal[CheckpointRecordKind.EXTENSION] = field(
        default=CheckpointRecordKind.EXTENSION, kw_only=True
    )

    def __post_init__(self) -> None:
        if (
            "." not in self.tag
            or any(not part for part in self.tag.split("."))
            or self.tag in SESSION_EVENT_TYPES
        ):
            raise ValueError("Checkpoint extensions require an application event tag")

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


@dataclass(frozen=True, config=_CONFIG)
class CheckpointRequest:
    """A recorded model request in a ``SessionExecution``, with messages by reference.

    ``messages`` lists the request's messages in order. A string is the ID of a
    message equal to the latest earlier message with that ID, taken from the
    state's history or from an earlier request of the same execution. Every
    other message, such as one a ``PrepareRequest`` hook or compaction changed,
    is stored in full, so the rebuilt request equals the one the model received.
    ``event`` is the recorded event without its request messages.
    """

    __hash__ = None

    event: ModelRequestedEvent
    messages: tuple[str | Message, ...]
    type: Literal[CheckpointRecordKind.REQUEST] = field(
        default=CheckpointRecordKind.REQUEST, kw_only=True
    )


type CheckpointEntry = Annotated[LogEvent | CheckpointExtension, Field(discriminator="type")]
type ExecutionEntry = Annotated[
    LogEvent | CheckpointExtension | CheckpointRequest, Field(discriminator="type")
]
type CheckpointSeed = Annotated[
    InboxAddedEvent | ApprovalRequestedEvent | ApprovalResolvedEvent, Field(discriminator="type")
]


@dataclass(frozen=True, config=_CONFIG)
class SessionExecution:
    """A checkpoint's execution part, without its conversation state.

    Store ``checkpoint.state`` when ``SessionCommit.state_changed`` is true and
    this part at every commit, then rebuild the checkpoint with
    ``SessionCheckpoint.from_parts(state, execution)``. Inside a turn its size
    depends on that turn, not on earlier history or retained assets: recorded
    model requests are ``CheckpointRequest`` values that refer to the state's
    messages by ID. ``state_sha256`` binds it to the state it was captured with.
    """

    __hash__ = None

    session_id: Annotated[str, Field(min_length=1)]
    state_sequence: Annotated[int, Field(ge=0)]
    state_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] = field(kw_only=True)
    offset: Annotated[int, Field(ge=0)]
    control: SessionControlChangedEvent
    seed: tuple[CheckpointSeed, ...] = ()
    events: tuple[ExecutionEntry, ...] = ()
    assets: tuple[AssetValue, ...] = ()
    files: tuple[TextValue, ...] = ()
    version: Literal[6] = 6

    def __post_init__(self) -> None:
        if self.offset < self.state_sequence:
            raise ValueError("An execution cannot start before its conversation state")

    def to_json(self) -> str:
        """Serialize the canonical versioned document in memory, without I/O."""
        return _EXECUTION.dump_json(self).decode("utf-8")

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the canonical JSON document as Python values, e.g. for a JSONB column."""
        return json.loads(self.to_json())

    @classmethod
    def from_dict(cls, payload: Mapping[str, JSONValue]) -> Self:
        """Validate a document returned by ``to_dict`` or decoded from ``to_json``."""
        return cls.from_json(_encode_document(payload, "Invalid session execution"))

    @classmethod
    def from_json(cls, payload: str | bytes) -> Self:
        """Validate structural data; combine it with its state to check the rest."""
        adapter = TypeAdapter(cls)
        try:
            return adapter.validate_json(payload)
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise SessionFormatError("Invalid session execution") from error


@dataclass(frozen=True, config=_CONFIG)
class SessionCheckpoint:
    """An immutable conversation boundary and its current bounded execution suffix.

    This is an SDK exchange value, not an application's database schema.
    Applications may map every field into their own records, but must reconstruct
    the complete checkpoint. It carries its ``session_id``, so reopening it with
    ``Agent.session(state=...)`` continues under the same ID. Serialization
    performs no I/O and requires no plugin catalog. Application event schemas are
    checked by the owning session before replay.
    """

    __hash__ = None

    session_id: Annotated[str, Field(min_length=1)]
    state: SessionState
    offset: Annotated[int, Field(ge=0)]
    control: SessionControlChangedEvent
    seed: tuple[CheckpointSeed, ...] = ()
    events: tuple[CheckpointEntry, ...] = ()
    assets: tuple[AssetValue, ...] = ()
    files: tuple[TextValue, ...] = ()
    version: Literal[5] = 5

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("Checkpoint session IDs must not be blank")
        if self.events and (
            not isinstance(self.events[0], TurnStartedEvent)
            or any(
                isinstance(event, TurnStartedEvent | TurnEndedEvent) for event in self.events[1:]
            )
        ):
            raise ValueError("A checkpoint suffix must describe one open turn")
        for event in (*self.seed, *self.events, self.control):
            if not isinstance(event, CheckpointExtension):
                validate_event(event)

    @property
    def execution(self) -> SessionExecution:
        """This checkpoint without its state, for storing state and execution apart."""
        return SessionExecution(
            session_id=self.session_id,
            state_sequence=self.state.next_sequence,
            state_sha256=self.state.content_sha256,
            offset=self.offset,
            control=self.control,
            seed=self.seed,
            events=_refer_messages(self.events, self.state),
            assets=self.assets,
            files=self.files,
        )

    @classmethod
    def from_parts(cls, state: SessionState, execution: SessionExecution) -> Self:
        """Rebuild a checkpoint from a stored state and an execution captured with it."""
        if (
            execution.state_sequence != state.next_sequence
            or execution.state_sha256 != state.content_sha256
        ):
            raise SessionFormatError("The execution belongs to a different conversation state")
        try:
            events = _resolve_messages(execution.events, state)
        except KeyError as error:
            raise SessionFormatError(
                "The execution refers to a message its state does not contain"
            ) from error
        try:
            return cls(
                session_id=execution.session_id,
                state=state,
                offset=execution.offset,
                control=execution.control,
                seed=execution.seed,
                events=events,
                assets=execution.assets,
                files=execution.files,
            )
        except (ValidationError, ValueError, TypeError) as error:
            raise SessionFormatError("Invalid session checkpoint") from error

    def to_json(self) -> str:
        """Serialize the canonical versioned document in memory, without I/O."""
        return _CHECKPOINT.dump_json(self).decode("utf-8")

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the canonical JSON document as Python values, e.g. for a JSONB column."""
        return json.loads(self.to_json())

    @classmethod
    def from_dict(cls, payload: Mapping[str, JSONValue]) -> Self:
        """Validate a document returned by ``to_dict`` or decoded from ``to_json``."""
        return cls.from_json(_encode_document(payload, "Invalid session checkpoint"))

    @classmethod
    def from_json(cls, payload: str | bytes) -> Self:
        """Validate structural data without needing application event classes."""
        try:
            return TypeAdapter(cls).validate_json(payload)
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise SessionFormatError("Invalid session checkpoint") from error


def _history_messages(state: SessionState) -> dict[str, Message]:
    return {entry.message.id: entry.message for entry in state.history}


def _refer_messages(
    events: tuple[CheckpointEntry, ...], state: SessionState
) -> tuple[ExecutionEntry, ...]:
    """Replace request messages equal to the latest known message with that ID."""
    known = _history_messages(state)
    entries: list[ExecutionEntry] = []
    for event in events:
        if not isinstance(event, ModelRequestedEvent):
            entries.append(event)
            continue
        slots: list[str | Message] = []
        for message in event.request.messages:
            slots.append(message.id if known.get(message.id) == message else message)
            known[message.id] = message
        request = replace(event.request, messages=())
        entries.append(CheckpointRequest(replace(event, request=request), tuple(slots)))
    return tuple(entries)


def _resolve_messages(
    entries: tuple[ExecutionEntry, ...], state: SessionState
) -> tuple[CheckpointEntry, ...]:
    """Rebuild request messages in the order ``_refer_messages`` recorded them."""
    known = _history_messages(state)
    events: list[CheckpointEntry] = []
    for entry in entries:
        if not isinstance(entry, CheckpointRequest):
            events.append(entry)
            continue
        messages: list[Message] = []
        for slot in entry.messages:
            message = known[slot] if isinstance(slot, str) else slot
            messages.append(message)
            known[message.id] = message
        request = replace(entry.event.request, messages=tuple(messages))
        events.append(replace(entry.event, request=request))
    return tuple(events)


def _encode_document(payload: Mapping[str, JSONValue], failure: str) -> str:
    try:
        return json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise SessionFormatError(failure) from error


_CHECKPOINT = TypeAdapter(SessionCheckpoint)
_EXECUTION = TypeAdapter(SessionExecution)
