"""The storage vocabulary of a persisted session: a boundary and its journal.

A session is stored as a *boundary*, a snapshot of the session at a rest point,
plus a *journal*: every durable event after that point, one entry per event.
Entries are only ever appended. Reopening loads the boundary and replays the
journal through the same projection the live session used.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, NamedTuple, Self

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
)
from harnessed.events.codec import CURRENT_VERSION, validate_event
from harnessed.events.unions import SESSION_EVENT_TYPES
from harnessed.models import Message
from harnessed.models.assets import AssetValue
from harnessed.models.values import VALUE_CONFIG, JSONMapping, JSONValue, to_dict

from .files import TextValue
from .state import HistoryEntry, SessionState

if TYPE_CHECKING:
    from harnessed.events.registry import EventRegistry

STORAGE_CONFIG = ConfigDict(**VALUE_CONFIG, ser_json_bytes="base64", val_json_bytes="base64")
"""Pydantic settings shared by every stored session document."""


class CheckpointRecordKind(StrEnum):
    """Storage-only record tags that sit beside built-in log events."""

    EXTENSION = "checkpoint.extension"
    REQUEST = "checkpoint.request"


@dataclass(frozen=True, config=STORAGE_CONFIG)
class CheckpointExtension:
    """Lossless application-event data, independent of a store's plugin catalog.

    Only the owning session decodes this data against its registered event class.
    Stores can round-trip it without importing application plugins. The payload
    is the explicit dynamic boundary; built-in events remain typed.
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


@dataclass(frozen=True, config=STORAGE_CONFIG)
class CheckpointRequest:
    """A recorded model request whose messages refer to earlier ones by ID.

    ``messages`` lists the request's messages in order. A string is the ID of a
    message equal to the latest earlier message with that ID: one from the
    boundary state's history, or one sent in an earlier request of the journal.
    Every other message, such as one a ``PrepareRequest`` hook or compaction
    changed, is stored in full, so the rebuilt request equals the one the model
    received. ``event`` is the recorded event without its request messages.
    """

    __hash__ = None

    event: ModelRequestedEvent
    messages: tuple[str | Message, ...]
    type: Literal[CheckpointRecordKind.REQUEST] = field(
        default=CheckpointRecordKind.REQUEST, kw_only=True
    )


type CheckpointEntry = Annotated[LogEvent | CheckpointExtension, Field(discriminator="type")]
"""A durable event as a checkpoint holds it: built-in and typed, or an application extension."""

type CheckpointRecord = Annotated[
    LogEvent | CheckpointExtension | CheckpointRequest, Field(discriminator="type")
]
"""A durable event in the form a journal entry stores it."""

type CheckpointSeed = Annotated[
    InboxAddedEvent | ApprovalRequestedEvent | ApprovalResolvedEvent, Field(discriminator="type")
]
"""Queued input or an approval grant that a boundary carries forward."""


@dataclass(frozen=True, config=STORAGE_CONFIG)
class SessionBoundary:
    """The session at a rest point: the snapshot that its journal continues from.

    A boundary holds everything the session needs at journal position
    ``sequence`` that no later entry restates: the conversation ``state``, the
    inputs still queued and the approval grants in force (``seed``), the run
    ``control``, and the resources the session stored beyond the state.

    Harnessed delivers a new boundary whenever the conversation state changes:
    with the first commit, at turn end, after idle compaction and after other
    idle changes. While a turn runs, the boundary is the one the turn started
    from, so its size does not change with the turn's progress.
    """

    __hash__ = None

    session_id: Annotated[str, Field(min_length=1)]
    sequence: Annotated[int, Field(ge=0)]
    state: SessionState
    control: SessionControlChangedEvent
    seed: tuple[CheckpointSeed, ...] = ()
    assets: tuple[AssetValue, ...] = ()
    files: tuple[TextValue, ...] = ()
    version: Literal[1] = 1

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("Boundary session IDs must not be blank")
        if self.sequence < self.state.next_sequence:
            raise ValueError("A boundary cannot precede its conversation state")
        for event in (*self.seed, self.control):
            validate_event(event)

    def to_json(self) -> str:
        """Serialize the canonical versioned document in memory, without I/O."""
        return _BOUNDARY.dump_json(self).decode("utf-8")

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the canonical JSON document as Python values, e.g. for a JSONB column."""
        return json.loads(self.to_json())

    @classmethod
    def from_dict(cls, payload: Mapping[str, JSONValue]) -> Self:
        """Validate a document returned by ``to_dict``."""
        return cls.from_json(encode_document(payload, "Invalid session boundary"))

    @classmethod
    def from_json(cls, payload: str | bytes) -> Self:
        """Validate a document returned by ``to_json``."""
        adapter = TypeAdapter(cls)
        try:
            return adapter.validate_json(payload)
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise SessionFormatError("Invalid session boundary") from error


@dataclass(frozen=True, config=STORAGE_CONFIG)
class JournalEntry:
    """One durable event at its journal position, in storage form.

    Entries are append-only: a delivered entry never changes, and a store keys
    it by its session and ``sequence``. ``record`` is the event itself, with two
    storage forms: an application event is a ``CheckpointExtension`` and a
    model request is a ``CheckpointRequest``. ``assets`` and ``files`` hold the
    resources the session stored after the previously delivered entry; a later
    file with the same key replaces an earlier one.
    """

    __hash__ = None

    sequence: Annotated[int, Field(ge=0)]
    record: CheckpointRecord
    assets: tuple[AssetValue, ...] = ()
    files: tuple[TextValue, ...] = ()
    version: Literal[1] = 1

    def __post_init__(self) -> None:
        match self.record:
            case CheckpointExtension():
                pass
            case CheckpointRequest(event=event):
                validate_event(event)
            case event:
                validate_event(event)

    def to_json(self) -> str:
        """Serialize the canonical versioned document in memory, without I/O."""
        return _ENTRY.dump_json(self).decode("utf-8")

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the canonical JSON document as Python values, e.g. for a JSONB column."""
        return json.loads(self.to_json())

    @classmethod
    def from_dict(cls, payload: Mapping[str, JSONValue]) -> Self:
        """Validate a document returned by ``to_dict``."""
        return cls.from_json(encode_document(payload, "Invalid journal entry"))

    @classmethod
    def from_json(cls, payload: str | bytes) -> Self:
        """Validate a document returned by ``to_json``."""
        adapter = TypeAdapter(cls)
        try:
            return adapter.validate_json(payload)
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            raise SessionFormatError("Invalid journal entry") from error


class MessageReferences:
    """The messages that a journal request may name by ID instead of storing them.

    Each turn starts from the history of the boundary it began at and then
    learns the messages of every request it records, in journal order. Outside
    a turn nothing is known. Encoding and decoding follow the same rule, so an
    ID always resolves to the very message the encoder compared it with.
    """

    def __init__(self, history: Iterable[HistoryEntry] = ()) -> None:
        self._known: dict[str, Message] = {entry.message.id: entry.message for entry in history}

    def refer(self, event: ModelRequestedEvent) -> CheckpointRequest:
        """Store a request, naming each message that equals the known one by its ID."""
        slots: list[str | Message] = []
        for message in event.request.messages:
            slots.append(_slot(message, self._known.get(message.id)))
            self._known[message.id] = message
        request = replace(event.request, messages=())
        return CheckpointRequest(replace(event, request=request), tuple(slots))

    def resolve(self, record: CheckpointRequest) -> ModelRequestedEvent:
        """Rebuild the request that ``refer`` stored."""
        messages: list[Message] = []
        for slot in record.messages:
            message = self._known.get(slot) if isinstance(slot, str) else slot
            if message is None:
                raise ValueError("The journal refers to a message its boundary does not contain")
            messages.append(message)
            self._known[message.id] = message
        request = replace(record.event.request, messages=tuple(messages))
        return replace(record.event, request=request)


class Resources(NamedTuple):
    """Images and text files that a session holds beside its events."""

    assets: tuple[AssetValue, ...] = ()
    files: tuple[TextValue, ...] = ()


class StoredResources:
    """The resources a store holds for a session, by asset digest and file key."""

    def __init__(self, resources: Resources | None = None) -> None:
        if resources is None:
            resources = Resources()
        self._assets = {value.ref.sha256 for value in resources.assets}
        self._files = {value.key: value for value in resources.files}

    @classmethod
    def of(cls, boundary: SessionBoundary) -> Self:
        """The resources a stored boundary holds, in its state and beside it."""
        state = boundary.state
        return cls(Resources((*state.assets, *boundary.assets), (*state.files, *boundary.files)))

    def missing(self, held: Resources) -> Resources:
        """The held resources that the store does not have in this exact form."""
        return Resources(
            tuple(value for value in held.assets if value.ref.sha256 not in self._assets),
            tuple(value for value in held.files if self._files.get(value.key) != value),
        )

    def add(self, resources: Resources) -> None:
        """Record resources the store now holds."""
        self._assets.update(value.ref.sha256 for value in resources.assets)
        self._files.update((value.key, value) for value in resources.files)


def attach_resources(
    entries: Sequence[JournalEntry], resources: Resources
) -> tuple[JournalEntry, ...]:
    """Put newly stored resources on the last entry, so they travel with it."""
    if not entries or (not resources.assets and not resources.files):
        return tuple(entries)
    *earlier, last = entries
    return (*earlier, replace(last, assets=resources.assets, files=resources.files))


def encode_document(payload: Mapping[str, JSONValue], failure: str) -> str:
    """Encode decoded JSON values for validation, failing with a format error."""
    try:
        return json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise SessionFormatError(failure) from error


def _slot(message: Message, known: Message | None) -> str | Message:
    if known is message:
        return message.id
    # Equal messages can still differ in type-sensitive JSON, such as tool
    # arguments or provider replay data, so equality is confirmed on the wire form.
    if (
        known is not None
        and known == message
        and _MESSAGE.dump_json(known) == _MESSAGE.dump_json(message)
    ):
        return message.id
    return message


_BOUNDARY = TypeAdapter(SessionBoundary)
_ENTRY = TypeAdapter(JournalEntry)
_MESSAGE = TypeAdapter(Message)
