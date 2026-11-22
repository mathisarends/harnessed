from dataclasses import field
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic.dataclasses import dataclass

from harnessed.models import AssistantMessage, Message, StopReason, UserMessage
from harnessed.models.values import VALUE_CONFIG, JSONValue

from .base import DurableEvent, Identifier, TransientEvent
from .kinds import EventKind


class MessageLifetime(StrEnum):
    SESSION = "session"
    TURN = "turn"


@dataclass(frozen=True, config=VALUE_CONFIG)
class SystemMessageEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.SYSTEM_MESSAGE] = field(default=EventKind.SYSTEM_MESSAGE, kw_only=True)
    content: str


@dataclass(frozen=True, config=VALUE_CONFIG)
class UserMessageEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.USER_MESSAGE] = field(default=EventKind.USER_MESSAGE, kw_only=True)
    message: UserMessage


@dataclass(frozen=True, config=VALUE_CONFIG)
class MessageAppendedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.MESSAGE_APPENDED] = field(
        default=EventKind.MESSAGE_APPENDED, kw_only=True
    )
    message: Message
    lifetime: MessageLifetime = MessageLifetime.SESSION
    turn_id: str | None = None


@dataclass(frozen=True, config=VALUE_CONFIG)
class PluginValueChangedEvent(DurableEvent):
    """Record the JSON value a plugin committed under its stable session key."""

    __hash__ = None

    type: Literal[EventKind.PLUGIN_VALUE_CHANGED] = field(
        default=EventKind.PLUGIN_VALUE_CHANGED, kw_only=True
    )
    key: Identifier
    value: JSONValue
    id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True, config=VALUE_CONFIG)
class AssistantMessageEvent(DurableEvent):
    """Close a model request with its accepted or interrupted partial message.

    An interrupted message is terminal for its request_id and is not followed
    by a ModelRequestFailedEvent for that same request.
    """

    __hash__ = None

    type: Literal[EventKind.ASSISTANT_MESSAGE] = field(
        default=EventKind.ASSISTANT_MESSAGE, kw_only=True
    )
    message: AssistantMessage
    request_id: Identifier = field(kw_only=True)
    interrupted: bool = False
    stop_reason: StopReason | None = None


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class TextDeltaEvent(TransientEvent):
    type: Literal[EventKind.TEXT_DELTA] = field(default=EventKind.TEXT_DELTA, kw_only=True)
    message_id: str
    text: str = ""


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class ReasoningDeltaEvent(TransientEvent):
    type: Literal[EventKind.REASONING_DELTA] = field(
        default=EventKind.REASONING_DELTA, kw_only=True
    )
    message_id: str
    text: str
