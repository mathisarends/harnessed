from dataclasses import replace
from typing import assert_type

from opentelemetry.sdk.trace import TracerProvider
from pydantic import JsonValue, SecretStr

from harnessed import Agent
from harnessed.integrations.opentelemetry import (
    ContentCapture,
    OpenTelemetryTracer,
    SecretText,
    TraceContent,
    TraceContentFilter,
    TraceContentKind,
    otlp_provider,
)


def redact(content: TraceContent) -> TraceContent | None:
    assert_type(content.kind, TraceContentKind)
    assert_type(content.value, JsonValue)
    assert_type(content.tool_name, str | None)
    return (
        None if content.kind is TraceContentKind.EXCEPTION_MESSAGE else replace(content, value={})
    )


content_filter: TraceContentFilter = redact
OpenTelemetryTracer(content_filter=content_filter)
OpenTelemetryTracer(content_filter=lambda content: "invalid")  # ty: ignore[invalid-argument-type]


class Credential:
    def get_secret_value(self) -> str:
        return "custom-token"


secret: SecretText = Credential()
capture = ContentCapture.redacted("raw-token", SecretStr("pydantic-token"), secret)
assert_type(capture, ContentCapture)
OpenTelemetryTracer(capture_content=capture, content_filter=content_filter)
ContentCapture.redacted(include_exception_messages=True)
ContentCapture.redacted(b"bytes")  # ty: ignore[invalid-argument-type]
ContentCapture.redacted(object())  # ty: ignore[invalid-argument-type]


async def configure() -> None:
    async with otlp_provider(
        service_name="my-app",
        endpoint="http://localhost:6006/v1/traces",
        resource_attributes={"openinference.project.name": "my-agent", "worker.count": 2},
    ) as provider:
        assert_type(provider, TracerProvider)
        tracer = OpenTelemetryTracer(tracer_provider=provider, capture_content=False)
        assert_type(tracer, OpenTelemetryTracer)
        agent = Agent(plugins=[tracer])
        assert_type(agent, Agent)
