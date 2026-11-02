import asyncio
import json
import re
from collections.abc import AsyncGenerator, Callable, Generator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import StrEnum
from typing import Any, Protocol, Self, runtime_checkable

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.util.types import AttributeValue
from pydantic import Field, JsonValue, SecretBytes

from harnessed.core.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolResult,
    ToolStatus,
)
from harnessed.core.model import ModelRequest, ModelUsage
from harnessed.core.plugin import Plugin, PluginConfig
from harnessed.core.runtime.control import StopAtBoundary
from harnessed.core.tracing import Span, Tracer
from harnessed.kernel import Context

_correlation: ContextVar[dict[str, str | int] | None] = ContextVar(
    "harnessed_trace_scope", default=None
)
_KINDS = {
    "turn": ("AGENT", "agent"),
    "step": ("CHAIN", "chain"),
    "model": ("LLM", "generation"),
    "tool": ("TOOL", "tool"),
}


@asynccontextmanager
async def otlp_provider(
    *,
    service_name: str,
    endpoint: str | None = None,
    resource_attributes: Mapping[str, AttributeValue] | None = None,
) -> AsyncGenerator[TracerProvider]:
    """Own a batched OTLP/HTTP provider for an application's async lifespan.

    Open once around all agent sessions and other instrumentation sharing this
    provider. Standard OpenTelemetry environment variables configure the exporter,
    resource and sampler; explicit arguments take precedence. ``service_name``
    overrides any resource attribute with the same name.

    The global provider is unchanged. Exiting shuts down this provider outside
    the event loop, including when setup or application work raises. Shutdown
    attempts to export pending batches; it does not confirm backend ingestion.
    """
    if not service_name.strip():
        raise ValueError("service_name must not be empty")
    provider = TracerProvider(
        resource=Resource.create(
            {
                **(resource_attributes if resource_attributes is not None else {}),
                "service.name": service_name,
            }
        ),
        shutdown_on_exit=False,
    )
    try:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        yield provider
    finally:
        await asyncio.to_thread(provider.shutdown)


class TraceContentKind(StrEnum):
    MODEL_INPUT = "model.input"
    MODEL_OUTPUT = "model.output"
    TOOL_ARGUMENTS = "tool.arguments"
    TOOL_RESULT = "tool.result"
    EXCEPTION_MESSAGE = "exception.message"


@dataclass(frozen=True, slots=True)
class TraceContent:
    """Portable export content, detached from the agent's messages and tool values.

    Filters may edit the copied JSON containers or return a replacement value.
    Exception messages must remain strings. Kind and tool identity describe the
    original field and cannot be changed by a filter.
    """

    kind: TraceContentKind
    value: JsonValue
    tool_name: str | None = None


type TraceContentFilter = Callable[[TraceContent], TraceContent | None]


@runtime_checkable
class SecretText(Protocol):
    """A text secret wrapper, including Pydantic's ``SecretStr``."""

    def get_secret_value(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ContentCapture:
    """Capture portable conversation and tool content with exact-value redaction.

    Secret values are snapshotted when the policy is created. Matching is literal
    and case-sensitive, including substrings and JSON object keys. Encoded or
    transformed copies and unknown secrets require application filtering.
    """

    include_exception_messages: bool = False
    _pattern: re.Pattern[str] | None = field(default=None, repr=False)

    @classmethod
    def redacted(cls, *secrets: str | SecretText, include_exception_messages: bool = False) -> Self:
        """Register nonempty secret values without inspecting application settings.

        Wrappers are unwrapped once. Empty values and invalid wrapper results
        raise without including their values in the error message. Recreate the
        policy when credentials rotate. The policy's repr omits secret values.
        """
        values: set[str] = set()
        for secret in secrets:
            if isinstance(secret, str):
                value = secret
            elif isinstance(secret, SecretText):
                try:
                    value = secret.get_secret_value()
                except Exception:
                    raise ValueError("Could not read a text secret") from None
            else:
                raise TypeError("Secrets must be strings or SecretText wrappers")
            if not isinstance(value, str):
                raise TypeError("SecretText wrappers must return strings")
            if not value:
                raise ValueError("Secret values must not be empty")
            values.add(value)
        # Lookahead finds overlapping values. Prefer the longest at each position
        # so a shorter registered prefix cannot leave a credential suffix behind.
        alternatives = "|".join(re.escape(value) for value in sorted(values, key=len, reverse=True))
        pattern = re.compile(f"(?=({alternatives}))") if values else None
        return cls(include_exception_messages=include_exception_messages, _pattern=pattern)

    def _redact(self, value: JsonValue) -> JsonValue:
        match value:
            case str():
                return self._redact_text(value)
            case dict():
                redacted: dict[str, JsonValue] = {}
                for key, item in value.items():
                    redacted_key = self._redact_text(key)
                    if redacted_key in redacted:
                        raise ValueError("Redaction produced duplicate object keys")
                    redacted[redacted_key] = self._redact(item)
                return redacted
            case list():
                return [self._redact(item) for item in value]
            case None | bool() | int() | float():
                return value
            case _:
                raise TypeError("Redacted content must contain only JSON values")

    def _redact_text(self, value: str) -> str:
        if self._pattern is None:
            return value
        parts: list[str] = []
        end = 0
        for found in self._pattern.finditer(value):
            start = found.start()
            if start >= end:
                parts.extend((value[end:start], "[redacted]"))
            end = max(end, start + len(found[1]))
        parts.append(value[end:])
        return "".join(parts)


class OpenTelemetryConfig(PluginConfig):
    capture_content: bool = False
    max_content_length: int = Field(default=16_384, ge=1)


class OpenTelemetryTracer(Plugin[OpenTelemetryConfig], slot="tracer"):
    """Export runtime spans through an application-owned OpenTelemetry provider.

    Import this module explicitly after installing ``harnessed[otel]``. The
    application configures exporters, batching and sampling and owns provider
    shutdown. Omitting the provider uses OpenTelemetry's global provider without
    replacing it. Content capture is opt-in and bounded per input/output field.
    """

    def __init__(
        self,
        *,
        tracer_provider: trace.TracerProvider | None = None,
        capture_content: bool | ContentCapture = False,
        max_content_length: int = 16_384,
        content_filter: TraceContentFilter | None = None,
    ) -> None:
        super().__init__(
            config=OpenTelemetryConfig(
                capture_content=True
                if isinstance(capture_content, ContentCapture)
                else capture_content,
                max_content_length=max_content_length,
            )
        )
        self.tracer_provider = tracer_provider
        self._capture_policy = (
            capture_content if isinstance(capture_content, ContentCapture) else None
        )
        if content_filter is not None and not callable(content_filter):
            raise TypeError("content_filter must be a synchronous callable or None")
        self.content_filter = content_filter

    async def setup(self, ctx: Context) -> None:
        self._tracer = trace.get_tracer("harnessed", tracer_provider=self.tracer_provider)
        ctx.provide(Tracer, self)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Generator[Span]:
        correlation = {} if name == "turn" else dict(_correlation.get() or {})
        for key in ("session_id", "turn_id", "step"):
            if isinstance(value := attributes.get(key), str | int):
                correlation[key] = value
        token = _correlation.set(correlation)
        try:
            with self._tracer.start_as_current_span(
                f"harnessed.{name}", record_exception=False, set_status_on_exception=False
            ) as native:
                kind, observation = _KINDS.get(name, ("CHAIN", "span"))
                native.set_attribute("openinference.span.kind", kind)
                native.set_attribute("langfuse.observation.type", observation)
                tool_name = attributes.get("tool")
                current = _Span(
                    native,
                    self.config,
                    self.content_filter,
                    self._capture_policy,
                    tool_name=tool_name if isinstance(tool_name, str) else None,
                )
                for key, value in {**correlation, **attributes}.items():
                    current.set_attribute(key, value)
                try:
                    yield current
                except StopAtBoundary:
                    native.set_attribute("harnessed.outcome", "stopped")
                    raise
                except BaseException as error:
                    if isinstance(error, asyncio.CancelledError):
                        native.set_attribute("harnessed.cancelled", True)
                    current.error(error)
                    raise
        finally:
            _correlation.reset(token)


class _Span:
    def __init__(
        self,
        native: trace.Span,
        config: OpenTelemetryConfig,
        content_filter: TraceContentFilter | None,
        capture_policy: ContentCapture | None,
        *,
        tool_name: str | None,
    ) -> None:
        self.native = native
        self.config = config
        self.content_filter = content_filter
        self.tool_name = tool_name
        self.capture_policy = capture_policy

    def set_attribute(self, name: str, value: Any) -> None:
        if not self.native.is_recording():
            return
        if isinstance(value, ModelRequest):
            if value.settings.max_tokens is not None:
                self.native.set_attribute("gen_ai.request.max_tokens", value.settings.max_tokens)
            if self.config.capture_content:
                self.content(
                    "input",
                    TraceContentKind.MODEL_INPUT,
                    [_message(message) for message in value.messages],
                )
        elif isinstance(value, ModelUsage):
            self.usage(value)
        elif isinstance(value, AssistantMessage):
            if self.config.capture_content:
                self.content("output", TraceContentKind.MODEL_OUTPUT, _message(value))
        elif isinstance(value, ToolCall):
            if self.config.capture_content:
                self.content("input", TraceContentKind.TOOL_ARGUMENTS, value.arguments)
        elif isinstance(value, ToolResult):
            self.native.set_attribute("harnessed.tool.status", value.status.value)
            self.native.set_attribute("harnessed.tool.is_error", value.is_error)
            if value.exit_code is not None:
                self.native.set_attribute("harnessed.tool.exit_code", value.exit_code)
            if value.is_error:
                description = (
                    "nonzero_exit_code" if value.status == ToolStatus.OK else value.status.value
                )
                self.native.set_status(trace.StatusCode.ERROR, description)
            if self.config.capture_content:
                self.content("output", TraceContentKind.TOOL_RESULT, value.content)
        elif isinstance(value, str | bool | int | float):
            self.native.set_attribute(f"harnessed.{name}", value)
            if name == "session_id":
                self.native.set_attribute("session.id", value)
            elif name == "model":
                self.native.set_attribute("llm.model_name", value)
                self.native.set_attribute("gen_ai.request.model", value)
            elif name == "tool":
                self.native.set_attribute("tool.name", value)

    def usage(self, usage: ModelUsage) -> None:
        attributes = {
            "llm.token_count.prompt": usage.input_tokens,
            "llm.token_count.completion": usage.output_tokens,
            "llm.token_count.total": usage.total_tokens,
            "llm.token_count.prompt_details.cache_read": usage.cache_read_tokens,
            "llm.token_count.prompt_details.cache_write": usage.cache_write_tokens,
            "llm.token_count.completion_details.reasoning": usage.reasoning_tokens,
            "gen_ai.usage.input_tokens": usage.input_tokens,
            "gen_ai.usage.output_tokens": usage.output_tokens,
            "gen_ai.usage.cost": usage.cost,
            "llm.cost.total": usage.cost,
        }
        for key, value in attributes.items():
            if value is not None:
                self.native.set_attribute(key, value)

    def content(self, direction: str, kind: TraceContentKind, value: Any) -> None:
        captured = self._capture(kind, value, field=direction)
        if captured is None:
            return
        try:
            serialized = json.dumps(captured.value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            self.native.set_attribute(f"harnessed.{direction}.serialization_error", True)
            return
        truncated = len(serialized) > self.config.max_content_length
        self.native.set_attribute(
            f"{direction}.value", serialized[: self.config.max_content_length]
        )
        self.native.set_attribute(
            f"{direction}.mime_type", "text/plain" if truncated else "application/json"
        )
        if truncated:
            self.native.set_attribute(f"harnessed.{direction}.truncated", True)

    def error(self, error: BaseException) -> None:
        if not self.native.is_recording():
            return
        self.native.set_status(trace.StatusCode.ERROR, type(error).__name__)
        attributes = {"exception.type": f"{type(error).__module__}.{type(error).__qualname__}"}
        if self.config.capture_content and (
            self.capture_policy is None or self.capture_policy.include_exception_messages
        ):
            captured = self._capture(
                TraceContentKind.EXCEPTION_MESSAGE, str(error), field="exception"
            )
            if captured is not None and isinstance(captured.value, str) and captured.value:
                attributes["exception.message"] = captured.value[: self.config.max_content_length]
        self.native.add_event("exception", attributes)

    def _capture(self, kind: TraceContentKind, value: Any, *, field: str) -> TraceContent | None:
        try:
            captured = TraceContent(kind, _trace_value(value), self.tool_name)
        except (TypeError, ValueError, RecursionError):
            self.native.set_attribute(f"harnessed.{field}.serialization_error", True)
            return None
        try:
            filtered = captured if self.content_filter is None else self.content_filter(captured)
            if filtered is not None:
                if not isinstance(filtered, TraceContent):
                    raise TypeError("Content filters must return TraceContent or None")
                if filtered.kind is not kind or filtered.tool_name != self.tool_name:
                    raise ValueError("Content filters cannot change the field's identity")
                if kind is TraceContentKind.EXCEPTION_MESSAGE and not isinstance(
                    filtered.value, str
                ):
                    raise TypeError("Filtered exception messages must be strings")
                if self.capture_policy is not None:
                    filtered = replace(filtered, value=self.capture_policy._redact(filtered.value))
            return filtered
        except Exception:
            # A redaction failure must never fall back to exporting the original
            # value or leak the filter's own exception message.
            self.native.set_attribute(f"harnessed.{field}.filter_error", True)
            return None


def _trace_value(value: Any) -> Any:
    # This export boundary copies supported runtime values without invoking
    # application serializers, which may deliberately expose wrapped secrets.
    if isinstance(value, SecretText | SecretBytes):
        return "[redacted]"
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _trace_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: _trace_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_trace_value(item) for item in value]
    return value


def _message(message: Message) -> dict[str, Any]:
    # Provider state and reasoning can include opaque or signed payloads; capture
    # only the portable conversation and tool arguments.
    value: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        value["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        value["tool_call_id"] = message.tool_call_id
    return value


__all__ = [
    "ContentCapture",
    "OpenTelemetryConfig",
    "OpenTelemetryTracer",
    "SecretText",
    "TraceContent",
    "TraceContentFilter",
    "TraceContentKind",
    "otlp_provider",
]
