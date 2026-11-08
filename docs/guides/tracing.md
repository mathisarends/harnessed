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
results, turn summaries, tool definitions, resolved generation settings and output
schemas, including copies in subsequent model history. For example, if a tool
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

The limit counts characters separately for each serialized content field and
exception message. Content fields always contain valid `application/json`.
After filtering and redaction, structural truncation shares the budget across
sibling values, shortens strings and omits array items or object fields. Arrays
retain their most recent items in order when all items cannot fit. This keeps a
large early observation from consuming every later message's budget. It is a
lossy diagnostic view, not a replayable conversation. At extremely small limits,
only an empty container or a scalar placeholder (`0` for a one-character budget)
may fit.

Truncated fields have `harnessed.<field>.truncated`, `original_length` (serialized
characters after filtering/redaction, before truncation) and `omitted_items`
(dropped array items and object fields throughout the value). Shortened strings
do not add to the item count. Exception messages remain plain text with their
own character limit. Serialization failures omit the field and set a marker.
Opaque provider state, reasoning and stack traces are excluded even with capture
enabled.

### Turn summaries and request details

The root `harnessed.turn` span captures only user messages accepted in that turn,
including steering consumed by later steps. It excludes previous turns, pending
followups, system instructions and browser observations. Rejected input that was
never accepted is absent. A resumed turn includes its previously accepted
messages. Its output is the last portable assistant message in that turn, with
`validated_output` when that exact message has an accepted structured-output
verdict. No Python application serializer runs. A turn without an assistant
message has no captured output.

`harnessed.outcome` distinguishes completion, limits, stops, aborts and failures;
`harnessed.output.partial` marks interrupted or incomplete answers. A failed
model stream can therefore retain a redacted partial answer without presenting
it as a completed result. Turn summaries pass through the same content policy
and length limits as model/tool content.

Model spans retain the message list in `input.value` and expose these separate,
independently bounded JSON fields:

| Field | Captured value |
| --- | --- |
| `request.tools.value` | Portable tool names, descriptions and parameter schemas. |
| `request.settings.value` | Resolved `max_tokens`, `temperature` and JSON provider options. |
| `request.output_schema.value` | Output contract identity, schema and mode, when configured. |

Each field also has a `.mime_type` and uses the truncation/error markers above.
Numeric `gen_ai.request.max_tokens` and `gen_ai.request.temperature` remain
metadata even without content capture. The settings describe Harnessed's
resolved request, not additional defaults or transformations inside a provider
SDK. Native client configuration and raw HTTP bodies are not captured.

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
| `TURN_INPUT` (`turn.input`) | User messages accepted within this turn |
| `TURN_OUTPUT` (`turn.output`) | Last assistant message, with `validated_output` when available |
| `MODEL_INPUT` (`model.input`) | Portable message list, including historical tool-call arguments and tool results |
| `MODEL_OUTPUT` (`model.output`) | Portable assistant message, including tool-call arguments |
| `MODEL_TOOLS` (`model.tools`) | Portable tool-definition list |
| `MODEL_SETTINGS` (`model.settings`) | Resolved generation settings and JSON provider options |
| `MODEL_OUTPUT_SCHEMA` (`model.output_schema`) | Portable output schema, when configured |
| `TOOL_ARGUMENTS` (`tool.arguments`) | Arguments object; `tool_name` identifies the executing tool |
| `TOOL_RESULT` (`tool.result`) | Tool content; `tool_name` identifies the executing tool |
| `EXCEPTION_MESSAGE` (`exception.message`) | Full exception message before truncation |

Each captured field passes through the filter independently, so tool arguments
can appear in both a model message and a tool span. The filter sees the complete
portable value before serialization and truncation. Provider state and reasoning
are already excluded. IDs, names, timings, usage and error types remain metadata
outside content filtering and redaction. Omitting a tool-result field leaves
copies in model history and final turn summaries available to separate filtering.
For sensitive data, apply rules to every kind that can carry a copy, including
turn/model inputs and outputs and request details.

A filter or redaction exception or invalid return omits the field and sets
`harnessed.<field>.filter_error`, for example `harnessed.input.filter_error`,
`harnessed.request.tools.filter_error` or `harnessed.exception.filter_error`.
Neither the original content nor the filter
exception message is exported as a fallback, and the agent's operation retains
its original outcome. Invalid JSON output is omitted with the existing
serialization-error marker. Independent HTTP or model instrumentors have their
own capture settings and do not use this callback.

## Library spans and hook instrumentation

Use `@traced` for whole functions and `record(...)` for explicit diagnostic
attributes. These public, provider-independent helpers reuse Browserrig's
sync/async wrapper pattern with Harnessed's ambient context. A browser library
can keep its function bodies and decorators, using its own `Page` and
`Observation` types:

```python
from harnessed.tracing import record, traced


@traced
async def capture_page(page: Page) -> Observation:
    observation = await page.observe()
    record(elements=len(observation.elements))
    return observation
```

Each call opens a span named `<module>.<qualified function name>`, with
`code.function.name`, `code.file.path` and `code.line.number`. Source metadata is
computed once at decoration. The decorator preserves signatures, annotations,
docstrings, method binding and coroutine detection. Coroutine spans cover the
complete awaited call, including error and cancellation cleanup. Without a
configured tracer, the wrapper calls the function directly. It does not capture
function arguments or return values. Apply `@traced` below `@tool`, `@classmethod`
or `@staticmethod`. Generator and async-generator functions are rejected; use a
`span()` inside their body to measure iteration rather than generator creation.

`record(**attributes)` adds scalar attributes to the innermost Harnessed span,
including spans opened by a decorator or the block API. Nested calls and
concurrent tasks keep their own active span, and every exit restores the parent.
Outside a span it is a no-op. These explicitly recorded values are metadata and
do not pass through the content filter/redaction policy; choose diagnostic
values rather than credentials or arbitrary application content.

For a portion of a function, use `span()` directly:

```python
from harnessed.tracing import span

with span("browserrig.wait_until_ready") as measured:
    ready = await wait_until_ready(page)
    measured.set_attribute("browserrig.readiness.ready", ready)
```

The facade also exports `current`, `Tracer` and `Span`. Importing it requires no
OTel extra and preserves canonical protocol identities. The synchronous context
manager stays active across `await`. Names are exported
exactly as supplied; qualified scalar attributes such as `code.function.name`
or `browserrig.readiness.ready` also retain their names. Unqualified scalar
attributes use the `harnessed.` prefix. The runtime explicitly names its spans
`harnessed.turn`, `harnessed.model`, etc.

`current()` returns the context's tracer, or a no-op. Runtime spans bind that
tracer for nested library calls and synchronous tools. The OTel integration also
binds it around invoked hooks, even when automatic hook spans are disabled.
Explicit compaction and `SessionClosing` hooks have session correlation without
reopening a completed turn. Context is restored on return, failure and
cancellation; concurrent sessions keep separate bindings.

`span(name, scope=owner, **attributes)` selects an explicit kernel `Scope` or
plugin `PluginScope`. It takes precedence over ambient context, including
selecting a no-op when that owner has no configured tracer. The selection stays
fixed for the open span. Setup/teardown code can use this form; work before the
tracer is available or after it is removed produces no spans. A library must
still own the lifetime of background tasks, which inherit their creation
context. Tracing context is not persisted in conversation snapshots.

Enable automatic invocation spans with
`OpenTelemetryTracer(tracer_provider=provider, trace_hooks=True)`. It defaults to
`False`. Every listener actually invoked gets a `harnessed.hook` span, including
custom kernel events. Metadata identifies the actual and registered event types
(`harnessed.hook.event`, `harnessed.hook.registered_event`), owner, priority and
`code.function.name`; event payloads are excluded. Skipped and disposed listeners
and Waterfall terminal defaults have no handler spans. Emit handlers are
siblings; Parallel handlers overlap; Waterfall continuations nest. A Waterfall
handler's duration includes awaited downstream work and is not its own exclusive
cost. Automatic source-location caching for hook handlers and duration-based
export filtering remain future work; decorated library functions already cache
their source metadata.

When adopting this API in Browserrig, replace its local `traced`, `record` and
`span` helpers with imports from `harnessed.tracing`. After the local tracing wrapper is migrated,
its `use_tracer` context and priority-sensitive `ExecuteTool`/`PreStep` binding
hooks can be removed. Existing `@traced` annotations and `record(...)` calls stay
in place. File paths now use the function's Python code filename rather than a
Browserrig repository root, so other libraries use the same implementation.

The kernel's optional `HookInstrumentation` protocol brackets invocation and
dependency binding without importing Core or OTel. It resolves from the
dispatching scope once per dispatch, preserving child overrides and the existing
ordering, Bail, Waterfall and cancellation contracts. Custom tracers use the
same `Tracer` and `Span` protocols: the runtime supplies typed request, usage,
message, tool-result and turn values for exporters to map. The default tracer
ignores these without serializing content.
