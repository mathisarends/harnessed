# Tracing with Phoenix, Langfuse and OpenTelemetry

Install the optional integration:

```bash
uv add 'harnessed[otel]'
```

`OpenTelemetryTracer` replaces the default `NoopTracer` in the `tracer` slot.
Import it from `harnessed.integrations.opentelemetry`; importing the core SDK or
`harnessed.plugins` does not import OpenTelemetry or require this extra.

## Open a provider for your application

Use `otlp_provider()` when the application does not already have an OpenTelemetry
provider. Open it once around all agents, sessions and other instrumentation that
share its export pipeline. The helper creates a provider with a batch processor
and an OTLP/HTTP exporter; it never installs or replaces the global provider.

```python
import asyncio

from harnessed import Agent
from harnessed.integrations.opentelemetry import OpenTelemetryTracer, otlp_provider


async def main() -> None:
    async with otlp_provider(
        service_name="my-app",
        resource_attributes={"openinference.project.name": "my-agent"},
    ) as provider:
        agent = Agent(
            model="openai:gpt-4.1-mini",
            plugins=[OpenTelemetryTracer(tracer_provider=provider)],
        )
        async with agent.session() as session:
            result = await session.run("Explain the Voyager program briefly.")
            print(result.text)


asyncio.run(main())
```

Install the model's provider extra separately, for example `harnessed[openai,otel]`.
Closing the helper shuts down its provider outside the event loop, including
after setup errors, application errors and task cancellation. Close active
sessions first, as the nesting above does. Batch exports run outside the agent
task. Closing a session or unmounting its plugin never shuts down the shared
provider or flushes on every turn. Shutdown attempts to drain pending batches;
check exporter diagnostics and the destination to confirm delivery.

With `endpoint=None`, the exporter reads standard OpenTelemetry environment
variables for endpoints, headers and timeouts. An explicit `endpoint=` takes
precedence and must be the full trace ingestion URL, including `/v1/traces` where
required. There is no implicit Phoenix endpoint. Resource environment variables
and sampling settings remain SDK-owned. Explicit resource attributes override
environment attributes, and `service_name` overrides `service.name` from either
source; the caller's mapping is not changed.

Content capture and size limits stay on `OpenTelemetryTracer`, independently of
the provider. Application CLI flags and optional imports stay at the entry point;
the helper lives in the explicitly imported optional integration. See the
[runnable example](examples.md#tracing) for offline console and OTLP modes.

## Reuse an existing application provider

An existing provider means the Python application already configures OpenTelemetry,
for example to instrument an API server's HTTP and database operations. A running
Phoenix server is the telemetry destination, not a provider in the application.
Pass the application's provider directly to each agent:

```python
# provider comes from the application's existing telemetry setup.
agent = Agent(
    model="openai:gpt-4.1-mini",
    plugins=[OpenTelemetryTracer(tracer_provider=provider)],
)
```

The application retains ownership of exporters, resources, sampling and shutdown;
`otlp_provider()` is not needed in this case. Its lifespan cleanup can run
`await asyncio.to_thread(provider.shutdown)` after all active work has closed.
Alternatively, omit `tracer_provider` to use OpenTelemetry's global provider. The
plugin never replaces or shuts down that provider. Without SDK/exporter setup,
OpenTelemetry's normal no-op behavior remains in place; exporter environment
variables alone do not create a pipeline.

For custom spans, use the same provider's public OTel API:

```python
application_tracer = provider.get_tracer("my-app")
with application_tracer.start_as_current_span("application.request"):
    result = await agent.run("Explain the Voyager program briefly.")
```

Custom spans opened inside an agent tool inherit that tool's context as well.
Operations outside an active context, such as browser startup or independent
background events, need their own explicit parent context to join an agent trace.

## Phoenix

For a local Phoenix server, set the signal-specific exporter endpoint:

```bash
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:6006/v1/traces
```

For a remote instance use its collector URL and authentication settings. Keep
`openinference.project.name` on the provider resource to select the project.
Alternatively, `phoenix.otel.register(...)` from `arize-phoenix-otel` can configure
the provider; pass its return value to `OpenTelemetryTracer`.
See [Phoenix tracing setup](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/setup-using-phoenix-otel).

## Langfuse

For Langfuse Cloud's EU region:

```bash
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://cloud.langfuse.com/api/public/otel/v1/traces
OTEL_EXPORTER_OTLP_TRACES_HEADERS=Authorization=Basic%20<base64-public-key-colon-secret-key>,x-langfuse-ingestion-version=4
```

Encode the project's `public_key:secret_key` as Base64 and substitute the result
for the placeholder. `%20` encodes the space in `Basic ...` for OpenTelemetry's
header environment variable parser. For a self-hosted instance, replace the host
and retain `/api/public/otel/v1/traces`. These settings use HTTP/protobuf; Langfuse
does not accept gRPC. The ingestion-version header enables Langfuse v4 real-time
ingestion. See [Langfuse's OTLP configuration and attribute mapping](https://langfuse.com/integrations/native/opentelemetry).

Every runtime span includes `session.id`, so turns in one conversation can be
grouped in either backend. To send to both backends, add two batch processors
with explicitly configured exporters to the same provider, or fan out through
an OpenTelemetry collector.

## Trace structure and captured values

```text
backend request (when an active parent exists)
  harnessed.turn
    harnessed.step
      harnessed.compaction (when needed)
      harnessed.model
      harnessed.approval_wait (when needed)
      harnessed.tool
      harnessed.tool (parallel sibling)
    harnessed.step
      harnessed.model
```

Each turn opens its own span, rather than keeping one trace open for an entire
conversation. The runtime inherits the active OpenTelemetry parent when its
driver task starts. This joins a normal `await agent.run(...)` to an instrumented
HTTP request; parallel tools and custom spans inside tools inherit their own
tool context. Use the same provider for request and agent instrumentation.

Queued turns processed by an already-running driver share that driver's parent
context; they are not automatically reparented to the later submitting HTTP
request. Reopening durable work inherits the resuming caller's context, rather
than resurrecting old trace IDs. Session, turn, request and tool-call IDs provide
correlation with externally captured execution events. Trace context is not part of a conversation snapshot.

The integration emits OpenInference span kinds, model names and token/cost
attributes for Phoenix, plus Langfuse observation types and recognized usage
attributes. Unknown cache/reasoning counters and cost remain absent; zero values
remain zero. Provider costs are reported only when supplied by the model.
Compaction has a separate span and usage counters. Tool timing starts after
approval and scheduler waits; denied calls have no execution span. A returned
tool error or nonzero exit code marks the tool span as failed even when the
agent recovers. Model failures and aborts close spans and retain partial usage
when the model reports it. Graceful stops are recorded as stopped, not failed.

Provider retries and provider-executed native tools remain inside the model
span; the integration does not instrument the provider's HTTP client. The
PydanticAI bridge keeps `instrument=False`, avoiding a second LLM span for the
same Harnessed request.

## Content capture

By default, spans contain IDs, timings, model/tool names, counters, outcomes and
exception types. Enable conversation and tool content with a redaction policy.
Register the application's text credentials directly, wherever they come from.

```python
import os

from harnessed.integrations.opentelemetry import ContentCapture, OpenTelemetryTracer

tracer = OpenTelemetryTracer(
    tracer_provider=provider,
    capture_content=ContentCapture.redacted(os.environ["BILLING_TOKEN"]),
    max_content_length=16_384,
)
```

This captures portable model inputs and outputs, tool-call arguments and tool
results, including copies in subsequent model history. For example, if a tool
returns `Authorization: Bearer <token>`, both its result span and the next model
input contain `Authorization: Bearer [redacted]`. Execution and session history
retain their original values.

`ContentCapture.redacted(*secrets, include_exception_messages=False)` accepts
strings and `SecretText` wrappers with a `get_secret_value() -> str` method.
Pydantic's `SecretStr` satisfies that protocol, as can application-owned wrappers.
Pass individual credentials from any configuration system.

```python
capture = ContentCapture.redacted(config.billing_token, config.crm_token)
```

Harnessed does not inspect settings objects or scan environment variables.
`pydantic-settings` is not required. Values are read once when the policy is
created, so recreate it when credentials rotate. Empty values, unsupported
types and wrappers returning non-strings raise errors without echoing values.
The policy's representation and the tracer's serializable configuration omit
registered values. `redacted()` with no arguments registers no literal secrets.

Matching is literal and case-sensitive. It masks occurrences inside strings,
nested arrays and objects, including object keys. Overlapping values are fully
masked. Encoded or transformed copies and unknown secrets require application
rules. Wrapped `SecretText` and Pydantic `SecretBytes` values encountered while
copying portable runtime data become `[redacted]` even without registration.
The trace conversion does not invoke application serializers.

Exception types and failure status are always available. Enable exception text
with `ContentCapture.redacted(token, include_exception_messages=True)` to apply
the same literal redaction before its length limit. `capture_content=True`
retains the plain boolean opt-in, including exception text, with no registered
literal secrets. Both forms mask wrapped secrets encountered during conversion.

The limit counts characters per serialized input/output field and exception
message. Truncated fields are marked and use `text/plain`; complete fields use
`application/json`. Serialization failures omit the field and set a marker.
Opaque provider state, reasoning and stack traces are excluded even with capture
enabled.

### Application filtering

Supply `content_filter` for additional application rules, such as omitting a
particular tool's result. The callback runs after the detached portable copy is
created and before registered secret values are masked, JSON serialization and
size limits. Registered values are masked in callback replacements as well.

```python
from harnessed.integrations.opentelemetry import (
    ContentCapture,
    OpenTelemetryTracer,
    TraceContent,
    TraceContentKind,
)


def omit_patient_result(content: TraceContent) -> TraceContent | None:
    if content.kind is TraceContentKind.TOOL_RESULT and content.tool_name == "lookup_patient":
        return None
    return content


tracer = OpenTelemetryTracer(
    tracer_provider=provider,
    capture_content=ContentCapture.redacted(config.billing_token),
    content_filter=omit_patient_result,
)
```

`TraceContentFilter` is a synchronous callable taking `TraceContent` and returning
`TraceContent | None`. It runs inline only when capture is enabled and the span
is recording. The frozen wrapper exposes `kind`, JSON-compatible `value` and
optional `tool_name`. Its JSON containers are detached copies: editing them
does not change model requests, tool arguments, results or session history.
Return `None` to omit the entire field, or return the same wrapper or a replacement
with an edited value. Preserve `kind` and `tool_name`; exception replacements
must remain strings. A JSON null is represented by `TraceContent(..., value=None)`
and is distinct from returning `None` to omit a field.

| Kind | Value before filtering |
| --- | --- |
| `MODEL_INPUT` (`model.input`) | Portable message list, including historical tool-call arguments and tool results |
| `MODEL_OUTPUT` (`model.output`) | Portable assistant message, including tool-call arguments |
| `TOOL_ARGUMENTS` (`tool.arguments`) | Arguments object; `tool_name` identifies the executing tool |
| `TOOL_RESULT` (`tool.result`) | Tool content; `tool_name` identifies the executing tool |
| `EXCEPTION_MESSAGE` (`exception.message`) | Full exception message before truncation |

Each captured field passes through the filter independently, so tool arguments
can appear in both a model message and a tool span. The filter sees the complete
portable value before serialization and truncation. Provider state and reasoning
are already excluded. IDs, names, timings, usage and error types remain metadata
outside content filtering and redaction. Omitting a tool-result field leaves
copies in model history available to separate filtering. For sensitive tool
data, apply rules to model input/output as well.

A filter or redaction exception or invalid return omits the field and sets
`harnessed.input.filter_error`, `harnessed.output.filter_error` or
`harnessed.exception.filter_error`. Neither the original content nor the filter
exception message is exported as a fallback, and the agent's operation retains
its original outcome. Invalid JSON output is omitted with the existing
serialization-error marker. Independent HTTP or model instrumentors have their
own capture settings and do not use this callback.

Custom tracer implementations still use the provider-independent
`harnessed.core.tracing.Tracer` and `Span` protocols. `span(name, **attributes)`
opens a synchronous context manager across asynchronous work. The runtime sets
typed `ModelRequest`, `ModelUsage`, `AssistantMessage`, `ToolCall` and `ToolResult` values
on the span; exporters map these values at the integration boundary. The default
tracer ignores them without serializing content.
