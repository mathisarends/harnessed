from __future__ import annotations

import json
from dataclasses import field, replace
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import AfterValidator, Field, NonNegativeInt, PositiveInt
from pydantic.dataclasses import dataclass

from .assets import AssetRef
from .values import VALUE_CONFIG, JSONMapping, NonEmptyText, freeze, to_dict


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class DenialSource(StrEnum):
    POLICY = "policy"
    USER = "user"
    REVIEWER = "reviewer"


class ContentKind(StrEnum):
    TEXT = "text"
    IMAGE_URL = "image_url"
    IMAGE_REFERENCE = "image_reference"


class ImageDetail(StrEnum):
    AUTO = "auto"
    LOW = "low"
    HIGH = "high"
    ORIGINAL = "original"


@dataclass(frozen=True, config=VALUE_CONFIG)
class MalformedArguments:
    """Lossless provider input that could not be parsed as a JSON object."""

    text: str


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolCall:
    __hash__ = None
    name: str
    arguments: JSONMapping = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    malformed: MalformedArguments | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.malformed is not None and self.arguments:
            raise ValueError("Malformed calls retain raw arguments instead of a parsed object")

    @property
    def command(self) -> str:
        command = self.arguments.get("command")
        if isinstance(command, str):
            return command
        argv = self.arguments.get("argv", ())
        return (
            " ".join(argv)
            if isinstance(argv, tuple) and all(isinstance(a, str) for a in argv)
            else ""
        )


@dataclass(frozen=True, config=VALUE_CONFIG)
class SpillRef:
    path: str
    sha256: str


@dataclass(frozen=True, config=VALUE_CONFIG)
class OutputTruncation:
    original_chars: PositiveInt
    retained_chars: NonNegativeInt
    max_chars: NonNegativeInt

    def __post_init__(self) -> None:
        if self.retained_chars >= self.original_chars or self.retained_chars > self.max_chars:
            raise ValueError("Invalid tool output truncation sizes")


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ApprovalSource(StrEnum):
    """Who supplied the final answer to an approval request."""

    USER = "user"
    REVIEWER = "reviewer"


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class ApprovalResponse:
    """A final answer with its origin, optional rationale and grant preference."""

    decision: Decision
    remember: bool = field(default=False, kw_only=True)
    source: ApprovalSource = field(default=ApprovalSource.USER, kw_only=True)
    reason: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.decision is Decision.ASK:
            raise ValueError("Answer an approval with ALLOW or DENY")
        if self.remember and self.decision is Decision.DENY:
            raise ValueError("Only an allow decision can be remembered")
        if self.remember and self.source is ApprovalSource.REVIEWER:
            raise ValueError("Reviewer approval applies to one request")


@dataclass(frozen=True, config=VALUE_CONFIG)
class TextPart:
    text: str
    type: Literal[ContentKind.TEXT] = ContentKind.TEXT


@dataclass(frozen=True, config=VALUE_CONFIG)
class ImagePart:
    url: str
    detail: ImageDetail = ImageDetail.AUTO
    type: Literal[ContentKind.IMAGE_URL] = ContentKind.IMAGE_URL


@dataclass(frozen=True, config=VALUE_CONFIG)
class ImageReference:
    """A stored image; resolved only for the model transport."""

    asset: Annotated[AssetRef, AfterValidator(AssetRef.require_image)]
    detail: ImageDetail = ImageDetail.AUTO
    type: Literal[ContentKind.IMAGE_REFERENCE] = ContentKind.IMAGE_REFERENCE


type ContentPart = Annotated[TextPart | ImagePart | ImageReference, Field(discriminator="type")]
type Content = str | tuple[ContentPart, ...]


def content_text(content: Content) -> str:
    return (
        content
        if isinstance(content, str)
        else "\n".join(part.text for part in content if isinstance(part, TextPart))
    )


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolResult:
    content: Content
    status: ToolStatus = ToolStatus.OK
    exit_code: int | None = None
    spill: SpillRef | None = None
    truncation: OutputTruncation | None = None
    denied_by: DenialSource | None = None
    rule_matches: tuple[tuple[str, bool], ...] = ()
    break_chain: bool = field(default=False, kw_only=True)

    def __post_init__(self) -> None:
        names = {name for name, _ in self.rule_matches}
        if len(names) != len(self.rule_matches):
            raise ValueError("Rule matches require unique names")

    @classmethod
    def error(cls, content: str) -> Self:
        return cls(content, ToolStatus.ERROR)

    @classmethod
    def denied(cls, content: str, *, source: DenialSource = DenialSource.POLICY) -> Self:
        return cls(content, ToolStatus.DENIED, denied_by=source)

    @classmethod
    def unavailable(cls, content: str) -> Self:
        return cls(content, ToolStatus.UNAVAILABLE)

    @property
    def is_error(self) -> bool:
        return self.status != ToolStatus.OK or self.exit_code not in (None, 0)

    def with_note(self, text: str) -> Self:
        if isinstance(self.content, str):
            return self.with_text(f"{self.content}\n\n{text}")
        return replace(self, content=(*self.content, TextPart(text)))

    @property
    def text(self) -> str:
        return content_text(self.content)

    def with_text(self, text: str) -> Self:
        """Replace textual content while retaining every image in its original order."""
        if isinstance(self.content, str):
            return replace(self, content=text)
        parts: list[ContentPart] = []
        inserted = False
        for part in self.content:
            if isinstance(part, TextPart):
                if not inserted:
                    parts.append(TextPart(text))
                    inserted = True
            else:
                parts.append(part)
        if not inserted:
            parts.insert(0, TextPart(text))
        return replace(self, content=tuple(parts))


@dataclass(frozen=True, kw_only=True, config=VALUE_CONFIG)
class ProviderState:
    """Versioned adapter-owned replay data, tied to its originating provider/model."""

    __hash__ = None
    format: NonEmptyText
    version: PositiveInt
    provider: NonEmptyText
    model: NonEmptyText
    data: JSONMapping


class _MessageText:
    @property
    def content(self) -> Content:
        raise NotImplementedError

    @property
    def text(self) -> str:
        return content_text(self.content)


class _PlainMessage(_MessageText):
    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return ()

    @property
    def reasoning(self) -> str:
        return ""

    @property
    def provider_state(self) -> ProviderState | None:
        return None


class _NoCallID:
    @property
    def tool_call_id(self) -> None:
        return None


@dataclass(frozen=True, config=VALUE_CONFIG)
class SystemMessage(_PlainMessage, _NoCallID):
    __hash__ = None
    content: str = ""
    id: str = field(default_factory=lambda: uuid4().hex, kw_only=True)
    role: Literal[Role.SYSTEM] = field(default=Role.SYSTEM, kw_only=True)


@dataclass(frozen=True, config=VALUE_CONFIG)
class UserMessage(_PlainMessage, _NoCallID):
    """Model input containing text or images, including application-supplied facts.

    The user role does not imply that a human authored the content.
    """

    __hash__ = None
    content: Content = ""
    id: str = field(default_factory=lambda: uuid4().hex, kw_only=True)
    role: Literal[Role.USER] = field(default=Role.USER, kw_only=True)


@dataclass(frozen=True, config=VALUE_CONFIG)
class AssistantMessage(_MessageText, _NoCallID):
    __hash__ = None
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning: str = ""
    id: str = field(default_factory=lambda: uuid4().hex, kw_only=True)
    provider_state: ProviderState | None = field(default=None, kw_only=True)
    role: Literal[Role.ASSISTANT] = field(default=Role.ASSISTANT, kw_only=True)


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolMessage(_PlainMessage):
    __hash__ = None
    content: Content = ""
    tool_call_id: NonEmptyText = field(kw_only=True)
    status: ToolStatus = field(default=ToolStatus.OK, kw_only=True)
    exit_code: int | None = field(default=None, kw_only=True)
    id: str = field(default_factory=lambda: uuid4().hex, kw_only=True)
    role: Literal[Role.TOOL] = field(default=Role.TOOL, kw_only=True)

    @property
    def is_error(self) -> bool:
        return self.status != ToolStatus.OK or self.exit_code not in (None, 0)


type Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolMessage, Field(discriminator="role")
]


def user(text: str) -> UserMessage:
    return UserMessage(text)


def system(text: str) -> SystemMessage:
    return SystemMessage(text)


def result_content(value: Any) -> str:
    return (
        value if isinstance(value, str) else json.dumps(to_dict(freeze(value)), ensure_ascii=False)
    )
