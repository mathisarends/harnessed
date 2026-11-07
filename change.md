# Proposal: trace hook handlers and library work

Status: design for review. This PR changes documentation only. The APIs and
behavior below are proposed, and are not available in the current release.
The roadmap records this work under future scope.

## Rationale

We need to explain where agent latency comes from: model calls, tool execution,
plugin preparation, readiness polling, or framework orchestration. Current
runtime spans cover turns, steps, models, tools, approval waits and compaction.
They do not identify the individual hook handlers running between those spans.

For example, a slow `BrowserPerception` handler of `PreStep` adds step latency,
but the trace does not show which handler captured the page or waited for it to
be ready. browserrig can instrument its own functions, but currently needs
priority-sensitive hooks and internal Harnessed imports to obtain the tracer.

This proposal combines automatic hook spans with a public tracing API and an
ambient tracer. Harnessed identifies each handler invocation; libraries add the
work-specific detail inside those invocations. No application hook needs an
extra decorator to appear in the trace.

## Top-down overview

Enable hook tracing once on the tracing plugin. The existing
`await scope.dispatch(event)` and plugin handler definitions stay the same.
Every handler that actually executes gets one `harnessed.hook` span.

```text
application enables OpenTelemetryTracer(trace_hooks=True)
  -> runtime binds the session's tracer
    -> Scope.dispatch(event)
      -> EventBus invokes each eligible listener
        -> neutral kernel instrumentation boundary
          -> Core bridge opens a hook span
            -> listener runs, awaits work, and optionally calls next_()
              -> library spans use the ambient tracer
          -> hook span closes on return, error, or cancellation
    -> optional duration filter decides which hook spans reach the exporter
```

Nested dispatches and Waterfall continuations follow the real call structure.
Sequential `Emit` handlers are siblings; `Parallel` handlers are overlapping
siblings. Handlers skipped by `Bail`, a short-circuited Waterfall, or disposal
produce no span. An event with no invoked listeners produces no hook spans.
The Waterfall default remains ordinary work inside the current parent span;
it is not presented as a registered plugin handler.

## Reading a trace

The following illustrative durations are wall-clock times, not measurements
from an existing run. The annotations identify attributes on `harnessed.hook`
spans; they are not additional event-dispatch spans.

```text
harnessed.turn
  harnessed.step                                      1,000 ms
    harnessed.hook [PreStep, BrowserPerception.capture]  180 ms
      browserrig.capture_page                           150 ms
        browserrig.wait_until_ready                     120 ms
        browserrig.extract_page                          30 ms
      harnessed.hook [PreStep, OtherPlugin.prepare]       20 ms
    harnessed.model                                     800 ms
```

Here BrowserPerception includes 150 ms of browser work and 20 ms in `next_()`.
The remaining 10 ms occurs elsewhere inside its invocation. The step has a
further 20 ms outside the shown hook and model spans; that remainder is not yet
attributed to a particular framework operation.

Three different questions need different measurements:

| Question | Interpretation |
| --- | --- |
| How long does this handler invocation take? | Its inclusive span duration, including `next_()`. |
| How much time does this handler contribute outside the downstream chain? | For an awaited, sequential continuation, inclusive duration minus time inside `next_()`. Its own library spans still count. |
| What happens inside the handler? | Its library spans, such as readiness polling and extraction. |

Backend span self-time usually excludes child spans. It therefore also excludes
the handler's own instrumented library work and must not be equated with the
plugin's total contribution. Computing time outside `next_()` is a separate
follow-up metric, not an extra exported timing attribute in this first design.

Overlapping intervals must not be added or subtracted as if they were sequential.
Tracing measures elapsed time, including I/O and suspension, and does not
replace CPU profiling. A missing span with filtering enabled does not prove
that an operation did not execute.

## Developer-facing API

All examples in this section describe the proposed API. Existing provider,
content-capture and redaction ownership remain as documented in the tracing
guide.

### Application configuration

```python
from harnessed import Agent
from harnessed.integrations.opentelemetry import OpenTelemetryTracer, otlp_provider

async with otlp_provider(
    service_name="browserrig",
    min_hook_duration_ms=1.0,
) as provider:
    agent = Agent(
        model=model,
        plugins=[
            OpenTelemetryTracer(
                tracer_provider=provider,
                trace_hooks=True,
            ),
            browser_plugin,
        ],
    )
    result = await agent.run("Inspect the page")
```

`trace_hooks` defaults to `False`; `min_hook_duration_ms` defaults to `0.0`.
Zero retains every recorded hook span. A positive minimum is finite and
nonnegative, is expressed in milliseconds, and applies only to automatic hook
spans. It does not suppress library, model, tool, or other runtime spans.

The minimum belongs to the provider's export pipeline. The earlier shorthand
`OpenTelemetryTracer(min_hook_duration_ms=...)` is not the proposed API: a
tracing plugin cannot retract a completed span from arbitrary processors on
an application-owned provider.

Applications with an existing SDK provider explicitly install the proposed
filtering processor on each export path that should use this policy:

```python
from opentelemetry.sdk.trace import TracerProvider

from harnessed.integrations.opentelemetry import HookSpanProcessor, OpenTelemetryTracer

provider = TracerProvider()
provider.add_span_processor(HookSpanProcessor(exporter, min_hook_duration_ms=1.0))
tracing = OpenTelemetryTracer(tracer_provider=provider, trace_hooks=True)
```

`HookSpanProcessor` is an optional integration type implementing the OTel
`SpanProcessor` contract. It owns a standard batch processor for its supplied
exporter and applies hook filtering before queuing completed spans. It replaces
the ordinary batch processor for that exporter. A separate unfiltered export
path will still receive the short spans.

The application owns provider shutdown. `otlp_provider()` continues to manage
the lifetime of the provider it creates. Closing a session does not shut down
the provider or flush all other sessions' traces.

### Library instrumentation

The explicit `harnessed.tracing` facade exports the existing `Tracer` and `Span`
protocol identities, plus these proposed helpers:

```python
def current() -> Tracer: ...


def span(
    name: str,
    *,
    scope: Scope | None = None,
    **attributes: Any,
) -> AbstractContextManager[Span]: ...
```

The attribute boundary keeps the existing dynamic value contract of `Tracer`;
the exporter validates and maps those values. Public implementation signatures
must retain the existing explicit attribute annotations.

`current()` returns the tracer bound to the current execution context, or a
no-op tracer. `span()` uses that tracer unless an explicit `scope` is provided.
An explicit scope takes precedence, including selecting a no-op when that scope
has no configured tracer. The selected tracer is captured for the span's
lifetime rather than following later service replacements.

```python
from harnessed.tracing import span

with span("browserrig.wait_until_ready") as measured:
    ready = await wait_until_ready(page)
    measured.set_attribute("browserrig.readiness.ready", ready)
```

The context manager is synchronous and remains active across `await`. Existing
library decorators can use it to instrument both synchronous and asynchronous
functions without passing `Scope` through every helper. A new public decorator
is outside this proposal.

Internal callers use the same canonical `span(name, scope=scope, ...)` form.
The existing `span(scope, name, ...)` helper is replaced at its call sites;
there is no transitional overload or duplicate protocol definition.

The facade imports no OTel SDK. It is registered in the executable architecture
policy as an SDK facade, and the lazy root SDK stays lazy.

### Span names and attributes

The exporter preserves caller-supplied span names exactly. Runtime call sites
explicitly use `harnessed.turn`, `harnessed.step`, `harnessed.model`, and the
other current runtime names. Library names such as `browserrig.Actions.run`
remain unchanged; punctuation is not used to guess a namespace.

Automatic hooks use `harnessed.hook` with these bounded metadata fields:

| Attribute | Value |
| --- | --- |
| `harnessed.hook.event` | Actual event's module-qualified class name. |
| `harnessed.hook.registered_event` | Listener's registered event class, including base-class subscriptions. |
| `harnessed.hook.owner` | Plugin diagnostic name, or a stable application-owner label. |
| `harnessed.hook.priority` | Registered integer priority. |
| `code.function.name` | Callback's module-qualified function or method name. |
| `code.file.path` | Source file, when available. |
| `code.line.number` | Source line, when available. |

Function names and source fields are cached per listener. Decorated functions,
bound methods, partials, and callable instances need defined best-effort
handling; unavailable source fields are omitted. Scope object addresses and
event payloads do not become span names or metadata.

Standard `code.*` attributes and explicitly qualified library attribute names
are exported as supplied. Existing runtime correlation and typed-value mapping
retain their semantics. Renaming internal call-site names must also update
the exporter's runtime-kind lookup and turn-correlation reset logic.

## Drill-down: abstraction boundary and invocation

The standalone kernel depends only on the standard library. In particular,
`kernel.events` cannot import `core.tracing` or an OTel module.

| Layer | Responsibility |
| --- | --- |
| Kernel | Describe and bracket one eligible listener invocation through a small `HookInstrumentation` protocol. |
| Core | Implement the bridge to a `Tracer`, bind the ambient tracer, and create the provider-independent span. |
| OTel integration | Register the Core bridge, translate spans and attributes, and provide optional export filtering. |
| Library | Mark meaningful internal work with the public tracing facade. |

The protocol and its immutable invocation metadata are exported through the
public `harnessed.kernel` facade. Its operation returns a synchronous context
manager around the invocation; it does not emit another event. The metadata
contains typed event, callback, priority and owner identities, not event data.

At dispatch entry, resolve the optional instrumentation service from the
dispatching scope. Keep that selection for this dispatch. A parent-owned
listener therefore uses the child session's service overrides, rather than
selecting a tracer from its registration owner's scope.

Invocation is schematically:

```text
recheck listener activity
activate the listener's registration owner
enter instrumentation context, if configured
  bind handler parameters from the dispatching scope
  invoke callback and await its resolved result
exit instrumentation context
```

This includes dependency binding and the handler's complete awaited execution.
Matching and sorting listeners remain outside individual handler spans. The
instrumentation must preserve priorities, stable ordering, single-use `next_()`,
`Bail` results including false values, and `Parallel` cancellation semantics.
The wrapper must neither invoke the callback twice nor swallow its exception.

With no instrumentation service, dispatch takes its direct invocation path and
does not inspect callback source locations or create span objects. A configured
real tracer may still bind context when `trace_hooks=False`, so library spans
inside hooks continue to work without automatic hook spans.

## Context lifetime, teardown and cancellation

Core binds a tracer in a `ContextVar` around turn execution and around session
operations that can call library code outside a turn, including compaction and
closing. The hook bridge also binds the dispatch-selected tracer for standalone
dispatch and inherited listeners. Binding and recording a hook span are
separate operations.

`TurnStopping` already executes inside a turn. `SessionClosing` executes after
the driver stops, so a turn-only binding would miss it. Its hook span remains
correlated by session ID and uses the active parent context if one exists; it
must not reopen a finished turn span.

Every context binding uses token reset in `finally`, including errors and
cancellation. Concurrent sessions and nested scopes must restore the previous
tracer, and an untraced child session must not accidentally use its parent's
tracer. Bindings and hook spans close before their owning scope is disposed.

Asyncio tasks inherit the creation context; tasks that outlive a handler can
therefore retain an ended parent context. Background work needs an explicit
lifetime and parent policy. The proposal does not silently reparent it or
persist active trace context in session snapshots.

## Minimum-duration filtering

Thresholds are evaluated after a span ends, using its complete inclusive
duration, including Waterfall `next_()`. They reduce exported volume, not the
cost of creating and timing a span. A duration sampler cannot make this
decision before execution.

The filtering processor observes spans from the same provider, including
ordinary library and directly created OTel spans, and follows this policy for
spans selected by the application's sampler:

| Completed hook span | Decision |
| --- | --- |
| Duration is at least the minimum | Export. |
| Error or cancellation | Export regardless of duration. |
| Below minimum, with no retained or still-active descendants | Drop. |
| Below minimum, with a retained descendant | Export to preserve its ancestry. |
| Below minimum, with a descendant already running when the hook ends | Conservatively retain the hook. |

For a 1 ms minimum, a successful 0.3 ms leaf hook is omitted, a 2 ms hook is
retained, and a 0.3 ms hook containing a visible library span is retained.
Non-hook spans are not duration-filtered. The policy never overrides head
sampling or reconstructs spans discarded by the application's sampler.

Hook identity attributes must be present when the SDK starts the span, so the
processor can identify it before children start. Retention propagates through
hook ancestors observed by this processor. Use bounded, thread-safe active-span
bookkeeping, with no network I/O on `on_start` or `on_end`; completed spans go to
the standard batch queue. At capacity, retain affected hooks rather than risk
dropping their ancestry. Remove bookkeeping on completion and provider shutdown.

A child span that first starts after an already dropped hook has ended cannot
retroactively restore that parent. Background work of that kind requires the
explicit lifetime policy described above. Ancestry guarantees apply to spans
observed on the configured provider and export path, not arbitrary providers.

Filtering must not change IDs, rewrite parent relationships, leave short spans
unclosed, mutate SDK private state, or buffer an entire long-running trace.
The plugin must not install hidden processors on an application-owned provider.
`force_flush()` delegates pending completed spans; provider shutdown performs
cleanup and exporter shutdown once through the normal OTel lifetime.

## Trade-offs

| Choice | Rationale and cost |
| --- | --- |
| Automatic handler instrumentation | Covers custom events and lifecycle hooks without depending on user hook priorities; adds a small Kernel contract. |
| Hook spans disabled by default | Avoids unexpected span volume for frequent events; developers explicitly enable them for diagnosis. |
| Ambient tracer plus explicit scope option | Keeps library functions small while preserving explicit session selection and testability. |
| Inclusive Waterfall spans | Mirrors the actual call tree; requires care when attributing plugin cost. |
| Threshold in the export pipeline | Makes filtering enforceable with public OTel APIs; existing providers need explicit processor wiring. |
| Keep parents of visible children | Preserves readable traces; some spans below the minimum remain visible. |
| Existing names preserved explicitly | Avoids exporter namespace guessing; internal runtime lookups need a coordinated update. |

General function auto-instrumentation, CPU profiling, provider-native retries,
and a new metrics backend are outside this change. Collector tail sampling
can be combined with this policy to select complete traces by error or latency.

## Risks and mitigations

- Frequent events can produce many spans and change the latency being measured.
  Keep automatic hook spans opt-in, cache source metadata, and benchmark both
  recording overhead and exported volume.
- Waterfall duration can be mistaken for the plugin's own contribution. Include
  the trace-reading example above and describe inclusive timing in the guide.
- Context inherited by background tasks can outlive the handler or session.
  Reset bindings on every exit and require an explicit background-work lifetime.
- Filtering can produce misleading ancestry or excessive bookkeeping. Observe
  the complete configured export path, preserve visible parents, bound state,
  and test direct OTel children as well as Harnessed-created spans.
- Changing name handling can break existing backend classification and
  correlation. Update runtime call sites and exporter lookups together, retaining
  the existing exported runtime names and testing Phoenix/Langfuse mappings.

## Rollout and browserrig adoption

1. Add the public facade and ambient binding, move internal span calls to the
   canonical signature and explicit names, and preserve backend mappings.
2. Add the neutral Kernel boundary, Core bridge, opt-in hook spans and lifecycle
   coverage. Document exactly which work is inside each measurement.
3. Add the optional filtering processor and helper configuration, together with
   hierarchy, concurrency and resource-lifetime tests.
4. Change browserrig's tracing wrapper to use `harnessed.tracing`. Remove its
   tracer-binding `ExecuteTool` and `PreStep` hooks after lifecycle coverage is
   verified. Keep its readiness, extraction and frame-discovery measurements.

## Review checklist and acceptance criteria

- [ ] Dispatch emits spans for actual invocations across `Emit`, `Bail`,
  `Waterfall`, `Parallel`, inherited subscriptions and nested dispatch.
- [ ] Ordering, false-valued bail results, short-circuiting, disposal, defaults,
  and single-use continuation behavior remain unchanged.
- [ ] Child dispatch scopes select their tracer even for parent-owned handlers;
  concurrent sessions, cancellation and replacement do not leak context.
- [ ] Free functions see the correct tracer in tools, `PreStep`, `TurnStopping`,
  standalone dispatch, compaction and `SessionClosing`, including when automatic
  hook spans are disabled.
- [ ] Failures and graceful stopping retain existing exception and redaction
  semantics. No event contents, arguments or callback representations are added.
- [ ] Exact-threshold, zero, invalid values, errors, cancellation, visible native
  OTel children, active children and filtered Waterfall chains follow the policy.
- [ ] Separate sessions, providers and export paths remain independent; bounded
  bookkeeping, flush and shutdown have observable outcome tests.
- [ ] Kernel and public facade import tests pass without OTel installed; facade
  exports preserve protocol identity and architecture checks remain clean.
- [ ] Source locations use the standard keys; library names are unchanged;
  Phoenix/Langfuse kinds and session/turn correlation still work.
- [ ] Benchmark identical workloads with no tracer, hook tracing disabled,
  enabled without filtering, and enabled with filtering. Report invocation
  overhead, end-to-end latency and exported span count separately. Include
  frequent `Emit`, Waterfall and parallel cases; do not claim timing savings
  merely because a filter exports fewer spans.

These boxes describe implementation work still to do. Passing the existing
tests validates the documentation baseline, not the proposed functionality.

## References

- [Current tracing guide](docs/guides/tracing.md)
- [Kernel dispatch implementation](harnessed/kernel/events.py)
- [Current tracing protocols](harnessed/core/tracing.py)
- [Current OTel integration](harnessed/integrations/opentelemetry.py)
- [OpenTelemetry code attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/code/)
- [OpenTelemetry Python span processor lifecycle](https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.html)
- [OpenTelemetry Python export processors](https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.export.html)
