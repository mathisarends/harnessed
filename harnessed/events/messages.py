from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from harnessed.models import AssistantMessage, Message, StopReason, UserMessage
from harnessed.models.messages import JSONValue, freeze

from .base import DurableEvent, Identifier, TransientEvent
from .kinds import EventKind


class MessageLifetime(StrEnum):
    SESSION = "session"
    TURN = "turn"


@dataclass(frozen=True)
class SystemMessageEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.SYSTEM_MESSAGE] = field(default=EventKind.SYSTEM_MESSAGE, kw_only=True)
    content: str


@dataclass(frozen=True)
class UserMessageEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.USER_MESSAGE] = field(default=EventKind.USER_MESSAGE, kw_only=True)
    message: UserMessage

    def __post_init__(self) -> None:
        if not isinstance(self.message, UserMessage):
            raise TypeError("UserMessageEvent requires a user message value")


@dataclass(frozen=True)
class MessageAppendedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.MESSAGE_APPENDED] = field(
        default=EventKind.MESSAGE_APPENDED, kw_only=True
    )
    message: Message
    lifetime: MessageLifetime = MessageLifetime.SESSION
    turn_id: str | None = None


@dataclass(frozen=True)
class PluginValueChangedEvent(DurableEvent):
    """Record the JSON value a plugin committed under its stable session key."""

    __hash__ = None

    type: Literal[EventKind.PLUGIN_VALUE_CHANGED] = field(
        default=EventKind.PLUGIN_VALUE_CHANGED, kw_only=True
    )
    key: str
    value: JSONValue
    id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("Plugin value keys must be non-empty strings")
        object.__setattr__(self, "value", freeze(self.value))


@dataclass(frozen=True)
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

    def __post_init__(self) -> None:
        if not isinstance(self.message, AssistantMessage):
            raise TypeError("AssistantMessageEvent requires an assistant message value")
        if self.stop_reason is not None:
            object.__setattr__(self, "stop_reason", StopReason(self.stop_reason))


@dataclass(frozen=True, slots=True)
class TextDeltaEvent(TransientEvent):
    type: Literal[EventKind.TEXT_DELTA] = field(default=EventKind.TEXT_DELTA, kw_only=True)
    message_id: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class ReasoningDeltaEvent(TransientEvent):
    type: Literal[EventKind.REASONING_DELTA] = field(
        default=EventKind.REASONING_DELTA, kw_only=True
    )
    message_id: str
    text: str
