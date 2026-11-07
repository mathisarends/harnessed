from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from harnessed.core.events.base import DurableEvent, Identifier, TransientEvent
from harnessed.core.events.kinds import EventKind
from harnessed.core.messages import AssistantMessage, Message, UserMessage
from harnessed.core.model import StopReason


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
class PluginInstructionsChangedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.PLUGIN_INSTRUCTIONS_CHANGED] = field(
        default=EventKind.PLUGIN_INSTRUCTIONS_CHANGED, kw_only=True
    )
    name: str
    value: str | None
    id: str = field(default_factory=lambda: uuid4().hex)


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
