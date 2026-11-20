from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator, Callable, Generator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import StrEnum
from typing import Any, Protocol, Self, runtime_checkable

from pydantic import Field, JsonValue, SecretBytes

from harnessed.events import (
    AssistantMessageEvent,
    OutputValidatedEvent,
    TurnEndedEvent,
    TurnOutcome,
    UserMessageEvent,
)
from harnessed.kernel import HookInstrumentation, PluginScope
from harnessed.models import (
    AssistantMessage,
    ModelRequest,
    ModelUsage,
    StopReason,
    ToolCall,
    ToolResult,
    ToolStatus,
)
from harnessed.runtime.control import StopAtBoundary
from harnessed.runtime.plugin import Plugin, PluginConfig
from harnessed.sessions.turns import Step, Turn, recorded_usage
from harnessed.tracing import HookTracing, Span, Tracer

from .openinference import (
    ConversationExport,
    MessageAttributes,
    TraceImage,
    limit_text,
    portable_message,
    portable_part,
)

try:
    import opentelemetry.trace as trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import SpanLimits, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.util.types import AttributeValue
except ModuleNotFoundError as error:
    if error.name is None or error.name.partition(".")[0] != "opentelemetry":
        raise
    _HAS_OTEL = False
else:
    _HAS_OTEL = True

_correlation: ContextVar[dict[str, str | int] | None] = ContextVar(
    "harnessed_trace_scope", default=None
)
_KINDS = {
    "harnessed.turn": ("AGENT", "agent"),
    "harnessed.step": ("CHAIN", "chain"),
    "harnessed.model": ("LLM", "generation"),
    "harnessed.tool": ("TOOL", "tool"),
}
# Attributes a model span can still receive after its request: usage, the
# response message, the stop reason and outcome or truncation markers.
_LATE_MODEL_ATTRIBUTES = 32


@asynccontextmanager
async def otlp_provider(
    *,
    service_name: str | None = None,
    endpoint: str | None = None,
    resource_attributes: Mapping[str, AttributeValue] | None = None,
    max_span_attributes: int | None = None,
) -> AsyncGenerator[TracerProvider]:
    """Own a batched OTLP/HTTP provider for an application's async lifespan.

    Open once around all agent sessions and other instrumentation sharing this
    provider. Standard OpenTelemetry environment variables configure the exporter,
    resource and sampler; explicit arguments take precedence. ``service_name``
    overrides any resource attribute with the same name. ``max_span_attributes``
    overrides ``OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT``; pass the same value to the
    tracer plugin so it budgets content for this limit.

    The global provider is unchanged. Exiting shuts down this provider outside
    the event loop, including when setup or application work raises. Shutdown
    attempts to export pending batches; it does not confirm backend ingestion.
    """
    _require_otel()
    attributes = dict(resource_attributes) if resource_attributes is not None else {}
    if service_name is not None:
        if not service_name.strip():
            raise ValueError("service_name must not be empty")
        attributes["service.name"] = service_name
    provider = TracerProvider(
        resource=Resource.create(attributes),
        shutdown_on_exit=False,
        span_limits=SpanLimits(max_span_attributes=max_span_attributes),
    )
    try:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        yield provider
    finally:
        await asyncio.to_thread(provider.shutdown)


def _require_otel() -> None:
    if not _HAS_OTEL:
        raise ImportError("Install the OpenTelemetry tracing extra with: uv add 'harnessed[otel]'")


class TraceContentKind(StrEnum):
    TURN_INPUT = "turn.input"
    TURN_OUTPUT = "turn.output"
    MODEL_INPUT = "model.input"
    MODEL_OUTPUT = "model.output"
    MODEL_TOOLS = "model.tools"
    MODEL_SETTINGS = "model.settings"
    MODEL_OUTPUT_SCHEMA = "model.output_schema"
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

    def get_secret_value(self) -> str:
        """Return the plaintext value to snapshot for exact-value redaction."""
        ...


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
    max_image_bytes: int = Field(default=5 * 1024 * 1024, ge=0)
    full_input_messages: bool = False
    trace_hooks: bool = False
    max_span_attributes: int | None = Field(default=None, ge=1)


class OpenTelemetryTracer(Plugin[OpenTelemetryConfig], slot="tracer"):
    """Export runtime spans through an application-owned OpenTelemetry provider.

    Import this module explicitly after installing ``harnessed[otel]``. The
    application configures exporters, batching and sampling and owns provider
    shutdown. Omitting the provider uses OpenTelemetry's global provider without
    replacing it.

    Content capture is opt-in. Model spans carry OpenInference message
    attributes: each text part and tool argument is limited to
    ``max_content_length`` characters, and each distinct image is exported once
    per session, up to ``max_image_bytes``. By default a model span lists only
    the messages that earlier model spans of the session did not show;
    ``full_input_messages`` exports the whole request instead. Turn and tool
    spans export one JSON value per field, bounded by ``max_content_length``.

    The OpenTelemetry SDK evicts a span's oldest attributes once it reaches its
    attribute count limit. Model spans therefore export only the newest input
    messages that fit within ``max_span_attributes``, omitting older messages
    whole and marking the input as truncated. The limit defaults to the SDK's
    environment-configured span limit (``OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT``,
    otherwise 128); set it when the provider uses explicit ``SpanLimits``.
    Correlation attributes such as ``harnessed.step`` and
    ``harnessed.request_id`` are restored if a span still overflows.
    """

    def __init__(
        self,
        *,
        tracer_provider: trace.TracerProvider | None = None,
        capture_content: bool | ContentCapture = False,
        max_content_length: int = 16_384,
        max_image_bytes: int = 5 * 1024 * 1024,
        full_input_messages: bool = False,
        content_filter: TraceContentFilter | None = None,
        trace_hooks: bool = False,
        max_span_attributes: int | None = None,
    ) -> None:
        _require_otel()
        super().__init__(
            config=OpenTelemetryConfig(
                capture_content=True
                if isinstance(capture_content, ContentCapture)
                else capture_content,
                max_content_length=max_content_length,
                max_image_bytes=max_image_bytes,
                full_input_messages=full_input_messages,
                trace_hooks=trace_hooks,
                max_span_attributes=max_span_attributes,
            )
        )
        self.tracer_provider = tracer_provider
        self._conversations: dict[str, ConversationExport] = {}
        self._capture_policy = (
            capture_content if isinstance(capture_content, ContentCapture) else None
        )
        if content_filter is not None and not callable(content_filter):
            raise TypeError("content_filter must be a synchronous callable or None")
        self.content_filter = content_filter

    async def setup(self, scope: PluginScope) -> None:
        self._tracer = trace.get_tracer("harnessed", tracer_provider=self.tracer_provider)
        configured = self.config.max_span_attributes
        self._attribute_limit = (
            configured if configured is not None else SpanLimits().max_span_attributes
        )
        # Each session sets up its own plugin copy; never share export state.
        self._conversations = {}
        scope.effect(self._conversations.clear)
        scope.provide(Tracer, self)
        scope.provide(HookInstrumentation, HookTracing(trace_hooks=self.config.trace_hooks))

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Generator[Span]:
        inherited = _correlation.get()
        correlation = dict(inherited) if inherited is not None else {}
        session = attributes.get("session_id")
        if name == "harnessed.turn" or (
            isinstance(session, str | int) and session != correlation.get("session_id")
        ):
            correlation = {}
        for key in ("session_id", "turn_id", "step"):
            if isinstance(value := attributes.get(key), str | int):
                correlation[key] = value
        token = _correlation.set(correlation)
        try:
            with self._tracer.start_as_current_span(
                name, record_exception=False, set_status_on_exception=False
            ) as native:
                kind, observation = _KINDS.get(name, ("CHAIN", "span"))
                tool_name = attributes.get("tool")
                session_id = correlation.get("session_id")
                conversation = (
                    self._conversations.setdefault(session_id, ConversationExport())
                    if isinstance(session_id, str)
                    else None
                )
                current = _Span(
                    native,
                    self.config,
                    self.content_filter,
                    capture_policy=self._capture_policy,
                    tool_name=tool_name if isinstance(tool_name, str) else None,
                    conversation=conversation,
                    attribute_limit=self._attribute_limit,
                )
                current.identify(
                    {
                        "openinference.span.kind": kind,
                        "langfuse.observation.type": observation,
                        **correlation,
                        **attributes,
                    }
                )
                try:
                    yield current
                except StopAtBoundary:
                    native.set_attribute("harnessed.outcome", "stopped")
                    raise
                except BaseException as error:
                    if name == "harnessed.turn":
                        native.set_attribute(
                            "harnessed.outcome",
                            "aborted" if isinstance(error, asyncio.CancelledError) else "failed",
                        )
                    if isinstance(error, asyncio.CancelledError):
                        native.set_attribute("harnessed.cancelled", True)
                    current.error(error)
                    raise
                finally:
                    current.finish()
        finally:
            _correlation.reset(token)


class OTLPTracing(OpenTelemetryTracer):
    """Export spans through a batched OTLP/HTTP provider owned by each session.

    With no arguments the OpenTelemetry SDK configures the service and exporter
    from the environment. Each activation drains its own batch queue on exit.
    """

    def __init__(
        self,
        *,
        service_name: str | None = None,
        endpoint: str | None = None,
        resource_attributes: Mapping[str, AttributeValue] | None = None,
        capture_content: bool | ContentCapture = False,
        max_content_length: int = 16_384,
        max_image_bytes: int = 5 * 1024 * 1024,
        full_input_messages: bool = False,
        content_filter: TraceContentFilter | None = None,
        trace_hooks: bool = False,
        max_span_attributes: int | None = None,
    ) -> None:
        super().__init__(
            capture_content=capture_content,
            max_content_length=max_content_length,
            max_image_bytes=max_image_bytes,
            full_input_messages=full_input_messages,
            content_filter=content_filter,
            trace_hooks=trace_hooks,
            max_span_attributes=max_span_attributes,
        )
        self.service_name = service_name
        self.endpoint = endpoint
        self.resource_attributes = resource_attributes

    async def setup(self, scope: PluginScope) -> None:
        self.tracer_provider = await scope.enter(
            otlp_provider(
                service_name=self.service_name,
                endpoint=self.endpoint,
                resource_attributes=self.resource_attributes,
                max_span_attributes=self.config.max_span_attributes,
            )
        )
        await super().setup(scope)


class _Span:
    def __init__(
        self,
        native: trace.Span,
        config: OpenTelemetryConfig,
        content_filter: TraceContentFilter | None,
        *,
        capture_policy: ContentCapture | None,
        tool_name: str | None,
        conversation: ConversationExport | None,
        attribute_limit: int | None,
    ) -> None:
        self._native = native
        self._attribute_limit = attribute_limit
        # Distinct keys this span received, mirroring the SDK's attribute count.
        self._keys: set[str] = set()
        self._identity: dict[str, AttributeValue] = {}
        self._identifying = False
        self.config = config
        self.content_filter = content_filter
        self.tool_name = tool_name
        self._capture_policy = capture_policy
        # Spans outside a session export their whole request, each image at most once.
        self._conversation = conversation if conversation is not None else ConversationExport()

    def identify(self, attributes: Mapping[str, Any]) -> None:
        """Set the span's kind and correlation attributes and remember them for ``finish()``."""
        self._identifying = True
        try:
            for name, value in attributes.items():
                self.set_attribute(name, value)
        finally:
            self._identifying = False

    def finish(self) -> None:
        """Restore identity attributes the SDK may have evicted, just before the span ends.

        The provider's actual limit is not public and may be below the configured
        budget, so identity is restored whenever the span grew past its identity.
        """
        if not self._native.is_recording() or len(self._keys) == len(self._identity):
            return
        # Setting a key again moves it to the newest position, so the SDK evicts
        # older content instead of correlation metadata.
        for name, value in self._identity.items():
            self._native.set_attribute(name, value)

    def set_attribute(self, name: str, value: Any) -> None:
        if not self._native.is_recording():
            return
        match value:
            case ModelRequest():
                self._request(value)
            case Step():
                self._aggregate_usage(recorded_usage(value.events))
            case Turn():
                self._turn(value)
            case ModelUsage():
                self.usage(value)
            case AssistantMessage():
                if self.config.capture_content:
                    self._output_message(value)
            case ToolCall():
                if self.config.capture_content:
                    self.content("input", TraceContentKind.TOOL_ARGUMENTS, value.arguments)
            case ToolResult():
                self._tool_result(value)
            case str() | bool() | int() | float():
                self._scalar(name, value)

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
                self._set(key, value)

    def content(self, direction: str, kind: TraceContentKind, value: Any) -> None:
        captured = self._capture(kind, value, field=direction)
        if captured is None:
            return
        try:
            serialized = json.dumps(captured.value, ensure_ascii=False, allow_nan=False)
            original_length = len(serialized)
            truncated = original_length > self.config.max_content_length
            omitted = 0
            if truncated:
                serialized, omitted = _limit_json(captured.value, self.config.max_content_length)
        except (TypeError, ValueError, RecursionError):
            self._set(f"harnessed.{direction}.serialization_error", True)
            return
        self._set(f"{direction}.value", serialized)
        self._set(f"{direction}.mime_type", "application/json")
        if truncated:
            self._set(f"harnessed.{direction}.truncated", True)
            self._set(f"harnessed.{direction}.original_length", original_length)
            self._set(f"harnessed.{direction}.omitted_items", omitted)

    def error(self, error: BaseException) -> None:
        if not self._native.is_recording():
            return
        self._native.set_status(trace.StatusCode.ERROR, type(error).__name__)
        attributes = {"exception.type": f"{type(error).__module__}.{type(error).__qualname__}"}
        if self.config.capture_content and (
            self._capture_policy is None or self._capture_policy.include_exception_messages
        ):
            captured = self._capture(
                TraceContentKind.EXCEPTION_MESSAGE, str(error), field="exception"
            )
            if captured is not None and isinstance(captured.value, str) and captured.value:
                attributes["exception.message"] = captured.value[: self.config.max_content_length]
        self._native.add_event("exception", attributes)

    def _request(self, request: ModelRequest) -> None:
        if request.settings.max_tokens is not None:
            self._set("gen_ai.request.max_tokens", request.settings.max_tokens)
        if request.settings.temperature is not None:
            self._set("gen_ai.request.temperature", request.settings.temperature)
        if not self.config.capture_content:
            return
        inherited = self._conversation.advance(
            request.messages, full=self.config.full_input_messages
        )
        self._set("harnessed.input_messages.inherited", inherited)
        self._tools(request)
        settings = self._capture(
            TraceContentKind.MODEL_SETTINGS, request.settings, field="request.settings"
        )
        if settings is not None:
            self._set_json("llm.invocation_parameters", settings.value, field="request.settings")
        if request.output_schema is not None:
            self.content(
                "request.output_schema",
                TraceContentKind.MODEL_OUTPUT_SCHEMA,
                request.output_schema,
            )
        # Messages come last: the remaining attribute budget decides how much
        # older history fits beside the request details.
        images: dict[str, TraceImage] = {}
        messages = [portable_message(message, images) for message in request.messages[inherited:]]
        self._messages("input", TraceContentKind.MODEL_INPUT, messages, images=images)

    def _tool_result(self, result: ToolResult) -> None:
        self._set("harnessed.tool.status", result.status.value)
        self._set("harnessed.tool.is_error", result.is_error)
        if result.exit_code is not None:
            self._set("harnessed.tool.exit_code", result.exit_code)
        if result.is_error:
            description = (
                "nonzero_exit_code" if result.status == ToolStatus.OK else result.status.value
            )
            self._native.set_status(trace.StatusCode.ERROR, description)
        if self.config.capture_content:
            content = result.content
            if not isinstance(content, str):
                # Tool spans name images by digest; their data belongs to model spans.
                images: dict[str, TraceImage] = {}
                content = [portable_part(part, images) for part in content]
            self.content("output", TraceContentKind.TOOL_RESULT, content)

    def _output_message(self, message: AssistantMessage) -> None:
        self._conversation.seen(message)
        images: dict[str, TraceImage] = {}
        self._messages(
            "output",
            TraceContentKind.MODEL_OUTPUT,
            portable_message(message, images),
            images=images,
        )

    def _messages(
        self,
        direction: str,
        kind: TraceContentKind,
        value: Any,
        *,
        images: dict[str, TraceImage],
    ) -> None:
        """Export filtered messages as OpenInference attributes, all or nothing."""
        captured = self._capture(kind, value, field=direction)
        if captured is None:
            return
        builder = MessageAttributes(
            text_limit=self.config.max_content_length,
            image_limit=self.config.max_image_bytes,
            images=images,
            conversation=self._conversation,
            span_id=format(self._native.get_span_context().span_id, "016x"),
        )
        messages = captured.value if kind is TraceContentKind.MODEL_INPUT else [captured.value]
        budget = self._message_budget() if kind is TraceContentKind.MODEL_INPUT else None
        try:
            attributes = builder.messages(f"llm.{direction}_messages", messages, budget=budget)
        except (TypeError, ValueError, RecursionError):
            self._set(f"harnessed.{direction}.serialization_error", True)
            return
        builder.commit()
        for key, value in attributes.items():
            self._set(key, value)
        if builder.truncated:
            self._set(f"harnessed.{direction}.truncated", True)
        if builder.omitted:
            self._set(f"harnessed.{direction}_messages.omitted", builder.omitted)

    def _tools(self, request: ModelRequest) -> None:
        captured = self._capture(TraceContentKind.MODEL_TOOLS, request.tools, field="request.tools")
        if captured is None:
            return
        if not isinstance(captured.value, list):
            self._set("harnessed.request.tools.serialization_error", True)
            return
        for index, definition in enumerate(captured.value):
            self._set_json(f"llm.tools.{index}.tool.json_schema", definition, field="request.tools")

    def _set_json(self, name: str, value: JsonValue, *, field: str) -> None:
        try:
            serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            self._set(f"harnessed.{field}.serialization_error", True)
            return
        limited = limit_text(serialized, self.config.max_content_length)
        self._set(name, limited.text)
        if limited.truncated:
            self._set(f"harnessed.{field}.truncated", True)

    def _scalar(self, name: str, value: AttributeValue) -> None:
        """Record a scalar under its harnessed name and the conventional aliases."""
        keys = [name if "." in name else f"harnessed.{name}"]
        if name == "session_id":
            keys.append("session.id")
        elif name == "model":
            keys.extend(("llm.model_name", "gen_ai.request.model"))
        elif name == "tool":
            keys.append("tool.name")
        for key in keys:
            self._set(key, value)
            if self._identifying:
                self._identity[key] = value

    def _set(self, name: str, value: AttributeValue) -> None:
        self._keys.add(name)
        self._native.set_attribute(name, value)

    def _message_budget(self) -> int | None:
        """Attributes left for input messages after this span's other attributes."""
        if self._attribute_limit is None:
            return None
        return max(0, self._attribute_limit - len(self._keys) - _LATE_MODEL_ATTRIBUTES)

    def _aggregate_usage(self, usage: ModelUsage) -> None:
        for key, value in {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cost": usage.cost,
        }.items():
            if value is not None:
                self._set(f"harnessed.usage.{key}", value)

    def _turn(self, turn: Turn) -> None:
        end = turn.events[-1]
        usage = end.usage if isinstance(end, TurnEndedEvent) else recorded_usage(turn.events)
        self._aggregate_usage(usage)
        if turn.reason is not None:
            self._set("harnessed.outcome", turn.reason.value)
        if not self.config.capture_content:
            return
        messages = [
            portable_message(event.message, {})
            for event in turn.events
            if isinstance(event, UserMessageEvent)
        ]
        self.content("input", TraceContentKind.TURN_INPUT, messages)
        final = next(
            (event for event in reversed(turn.events) if isinstance(event, AssistantMessageEvent)),
            None,
        )
        if final is None:
            return
        output = portable_message(final.message, {})
        proof = next(
            (
                event
                for event in reversed(turn.events)
                if isinstance(event, OutputValidatedEvent) and event.message_id == final.message.id
            ),
            None,
        )
        if proof is not None:
            output["validated_output"] = proof.data
        self._set(
            "harnessed.output.partial",
            final.interrupted
            or turn.reason != TurnOutcome.COMPLETED
            or (
                final.stop_reason not in (None, StopReason.END)
                and not (proof is not None and final.stop_reason is StopReason.TOOL_CALLS)
            ),
        )
        if final.stop_reason is not None:
            self._set("harnessed.output.stop_reason", final.stop_reason.value)
        self.content("output", TraceContentKind.TURN_OUTPUT, output)

    def _capture(self, kind: TraceContentKind, value: Any, *, field: str) -> TraceContent | None:
        try:
            captured = TraceContent(kind, _trace_value(value), self.tool_name)
        except (TypeError, ValueError, RecursionError):
            self._set(f"harnessed.{field}.serialization_error", True)
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
                if self._capture_policy is not None:
                    # The capture policy is defined in this module; redaction is not public API.
                    redacted = self._capture_policy._redact(filtered.value)  # noqa: SLF001
                    filtered = replace(filtered, value=redacted)
            return filtered
        except Exception:
            # A redaction failure must never fall back to exporting the original
            # value or leak the filter's own exception message.
            self._set(f"harnessed.{field}.filter_error", True)
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


def _limit_json(value: JsonValue, budget: int) -> tuple[str, int]:
    """Share the budget across siblings, keeping later conversation items visible."""
    serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(serialized) <= budget:
        return serialized, 0
    if budget < 2:
        return "0", len(value) if isinstance(value, list | dict) else 0
    match value:
        case str():
            return _limit_string(value, budget), 0
        case list():
            return _limit_list(value, budget)
        case dict():
            return _limit_object(value, budget)
        case _:
            return "0", 0


def _limit_string(value: str, budget: int) -> str:
    """The longest JSON string prefix that fits the budget."""
    low = 0
    high = min(len(value), budget - 2)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(value[:middle], ensure_ascii=False)) <= budget:
            low = middle
        else:
            high = middle - 1
    return json.dumps(value[:low], ensure_ascii=False)


def _limit_list(value: list[JsonValue], budget: int) -> tuple[str, int]:
    """Keep the latest items that fit and share the remaining budget between them."""
    selected: list[JsonValue] = []
    available = budget - 2
    for item in reversed(value):
        minimum = min(len(json.dumps(item, ensure_ascii=False, allow_nan=False)), 64)
        cost = minimum + (2 if selected else 0)
        if cost > available:
            break
        selected.append(item)
        available -= cost
    if not selected:
        return "[]", len(value)
    selected.reverse()
    count = len(selected)
    available = budget - 2 - 2 * (count - 1)
    fragments: list[str] = []
    omitted = len(value) - count
    for index, item in enumerate(selected):
        share = available // (count - index)
        fragment, dropped = _limit_json(item, share)
        fragments.append(fragment)
        omitted += dropped
        available -= len(fragment)
    return "[" + ", ".join(fragments) + "]", omitted


def _limit_object(value: dict[str, JsonValue], budget: int) -> tuple[str, int]:
    """Keep the keys that fit and share the remaining budget between their values."""
    entries: list[tuple[str, JsonValue]] = []
    available = budget - 2
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("Trace object keys must be strings")
        encoded = json.dumps(key, ensure_ascii=False)
        minimum = len(encoded) + 3 + (2 if entries else 0)
        if minimum <= available:
            entries.append((encoded, item))
            available -= minimum
    fragments: list[str] = []
    omitted = len(value) - len(entries)
    for index, (encoded, item) in enumerate(entries):
        share = 1 + available // (len(entries) - index)
        fragment, dropped = _limit_json(item, share)
        fragments.append(encoded + ": " + fragment)
        omitted += dropped
        available -= len(fragment) - 1
    return "{" + ", ".join(fragments) + "}", omitted


__all__ = [
    "ContentCapture",
    "OTLPTracing",
    "OpenTelemetryConfig",
    "OpenTelemetryTracer",
    "SecretText",
    "TraceContent",
    "TraceContentFilter",
    "TraceContentKind",
    "otlp_provider",
]
