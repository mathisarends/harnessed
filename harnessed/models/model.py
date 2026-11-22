"""Provider-independent model contracts; optional integrations live in harnessed.models."""

from collections.abc import AsyncIterator, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import field, replace
from enum import StrEnum
from typing import Annotated, Protocol, Self, runtime_checkable

from pydantic import Field
from pydantic.dataclasses import dataclass

from .diagnostics import ModelDiagnostic
from .messages import AssistantMessage, Message, ToolCall
from .values import VALUE_CONFIG, JSONMapping, NonEmptyText


@dataclass(frozen=True, slots=True, kw_only=True, config=VALUE_CONFIG)
class GenerationSettings:
    """Immutable model defaults or per-turn overrides; None inherits a default.

    Provider-specific JSON options are an explicit integration boundary. Common
    fields have one canonical location and cannot also appear in provider_options.
    """

    __hash__ = None
    max_tokens: Annotated[int, Field(ge=1)] | None = None
    temperature: Annotated[float, Field(ge=0)] | None = None
    provider_options: JSONMapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        if {"max_tokens", "temperature"} & self.provider_options.keys():
            raise ValueError("Use the typed generation fields for max_tokens and temperature")

    def override(self, settings: Self | None) -> Self:
        """Merge explicitly supplied values without mutating either configuration."""
        if settings is None:
            return self
        return replace(
            self,
            max_tokens=self.max_tokens if settings.max_tokens is None else settings.max_tokens,
            temperature=self.temperature if settings.temperature is None else settings.temperature,
            provider_options={**self.provider_options, **settings.provider_options},
        )


class StopReason(StrEnum):
    END = "end"
    TOOL_CALLS = "tool_calls"
    MAX_TOKENS = "max_tokens"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    MAX_STEPS = "max_steps"
    TOKEN_BUDGET = "token_budget"
    STOPPED = "stopped"
    ABORTED = "aborted"
    DISCARDED = "discarded"
    REJECTED = "rejected"


class OutputMode(StrEnum):
    AUTO = "auto"
    NATIVE = "native"
    TOOL = "tool"


class TokenLimitBehavior(StrEnum):
    ACCEPT = "accept"
    CONTINUE = "continue"
    FAIL = "fail"


class UsagePurpose(StrEnum):
    RESPONSE = "response"
    COMPACTION = "compaction"


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolDefinition:
    __hash__ = None
    name: NonEmptyText
    description: str
    parameters: JSONMapping


@dataclass(frozen=True, config=VALUE_CONFIG)
class OutputSchema:
    """A serializable response contract; Python output types stay in the host."""

    __hash__ = None
    id: NonEmptyText
    name: NonEmptyText
    json_schema: JSONMapping
    wrapped: bool = False
    mode: OutputMode = OutputMode.AUTO


@dataclass(frozen=True, config=VALUE_CONFIG)
class ModelRequest:
    __hash__ = None
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()
    settings: GenerationSettings = field(default_factory=GenerationSettings)
    output_schema: OutputSchema | None = None

    def append_message(self, message: Message) -> Self:
        """Return a request with one more message, retaining all other fields.

        This does not mutate this request or append to the session's history.
        """
        return replace(self, messages=(*self.messages, message))


def resolve_request_settings(request: ModelRequest, defaults: GenerationSettings) -> ModelRequest:
    """Resolve defaults before compaction, budgeting, audit and execution."""
    return replace(request, settings=defaults.override(request.settings))


@dataclass(frozen=True, config=VALUE_CONFIG)
class ModelUsage:
    """Provider-neutral counters; cache writes are distinct from uncached input."""

    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)] = 0
    cache_read_tokens: Annotated[int, Field(ge=0)] | None = None
    cache_write_tokens: Annotated[int, Field(ge=0)] | None = None
    reasoning_tokens: Annotated[int, Field(ge=0)] | None = None
    cost: Annotated[float, Field(ge=0)] | None = None

    @property
    def cache_hit_rate(self) -> float | None:
        if self.cache_read_tokens is None or self.input_tokens == 0:
            return None
        return self.cache_read_tokens / self.input_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def sum_usage(usages: Iterable[ModelUsage]) -> ModelUsage:
    values = tuple(usages)

    def optional[N: (int, float)](counts: Iterable[N | None]) -> N | None:
        values = iter(counts)
        total = next(values, None)
        if total is None:
            return None
        for value in values:
            if value is None:
                return None
            total += value
        return total

    input_tokens = sum(value.input_tokens for value in values)
    output_tokens = sum(value.output_tokens for value in values)
    return ModelUsage(
        input_tokens,
        output_tokens,
        optional(value.cache_read_tokens for value in values),
        optional(value.cache_write_tokens for value in values),
        optional(value.reasoning_tokens for value in values),
        optional(value.cost for value in values),
    )


class ToolArgumentsMode(StrEnum):
    APPEND = "append"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class ToolCallStart:
    """Start or replace a preview at a response-local part index."""

    index: Annotated[int, Field(ge=0)]
    name: str
    call_id: str | None = None


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class ToolCallDelta:
    """Unvalidated argument text, with the current name and provider call ID.

    APPEND adds a JSON fragment; REPLACE supplies a complete snapshot, used for
    providers that stream object patches. Neither form authorizes execution.
    """

    index: Annotated[int, Field(ge=0)]
    arguments: str
    name: str
    call_id: str | None = None
    mode: ToolArgumentsMode = ToolArgumentsMode.APPEND


@dataclass(frozen=True, config=VALUE_CONFIG)
class ModelChunk:
    __hash__ = None
    text: str = ""
    reasoning: str = ""
    tool_call: ToolCall | None = None
    usage: ModelUsage | None = None
    stop_reason: StopReason | None = None
    tool_call_start: ToolCallStart | None = None
    tool_call_delta: ToolCallDelta | None = None
    diagnostic: ModelDiagnostic | None = None


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class ModelInfo:
    name: NonEmptyText
    provider: NonEmptyText | None = field(default=None, kw_only=True)
    context_window: Annotated[int, Field(ge=1)] | None = None
    max_output: Annotated[int, Field(ge=1)] | None = None
    supports_images: bool = False
    supports_structured_output: bool = False


@dataclass(frozen=True, slots=True, config=VALUE_CONFIG)
class ModelResponse:
    __hash__ = None
    message: AssistantMessage
    usage: ModelUsage = field(default_factory=lambda: ModelUsage(0))
    stop_reason: StopReason = StopReason.END


class ModelStream(Protocol):
    def __aiter__(self) -> AsyncIterator[ModelChunk]: ...
    async def __anext__(self) -> ModelChunk: ...

    @property
    def usage(self) -> ModelUsage | None:
        """Cumulative usage so far, available after interruption; None if unknown."""
        ...

    def response(self) -> ModelResponse:
        """Return the final response after the stream has been fully consumed."""
        ...


@runtime_checkable
class Model(Protocol):
    @property
    def info(self) -> ModelInfo:
        """Describe the model identity, capabilities and context window."""
        ...

    @property
    def settings(self) -> GenerationSettings:
        """Immutable defaults, resolved into each request before execution."""
        ...

    async def request(self, request: ModelRequest) -> ModelResponse:
        """Execute one request using its resolved settings and return the complete response."""
        ...

    def stream(self, request: ModelRequest) -> AbstractAsyncContextManager[ModelStream]:
        """Own a streaming request until context exit, including interruption cleanup."""
        ...
