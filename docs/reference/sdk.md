# SDK reference

This page describes the implemented application API. Detailed behavior and complete
examples live in the topic guides. Optional features are imported from
`harnessed.plugins`; model contracts and message types come from `harnessed.models`,
and SDK errors from `harnessed.errors`.

## Tracing

`harnessed.tracing` explicitly exports the canonical provider-independent `Tracer`
and `Span` protocols, `current() -> Tracer` and
`span(name, *, scope=None, **attributes)`. The span is a synchronous context
manager that remains active across awaits. Names are used verbatim. An explicit
kernel `Scope` or `PluginScope` overrides the ambient tracer, including selecting
a no-op when no tracer is configured. Selection stays fixed for the open span;
bindings are restored on every exit path and are never persisted.

`traced(fn)` decorates Python functions and coroutine functions without changing
their signature or coroutine kind. Each call uses the module-qualified function
name and source metadata cached at decoration. Arguments and return values are
not automatically recorded. Apply below tool/classmethod/staticmethod decorators.
Generator functions are rejected; place a `span()` inside their iteration body.
`record(**attributes)` attaches scalar metadata to the innermost Harnessed span
and does nothing outside a span. It restores the parent after nested work,
exceptions and cancellation and isolates concurrent or untraced session contexts.
Explicitly recorded attributes are outside content filtering/redaction.

`harnessed.integrations.opentelemetry.OTLPTracing(*, service_name=None,
endpoint=None, resource_attributes=None, capture_content=False, max_content_length=16384,
max_image_bytes=5242880, full_input_messages=False, content_filter=None, trace_hooks=False)`
fills the `tracer` slot with a session-owned batched OTLP/HTTP exporter. Each session
closes its provider and waits for shutdown. Exporter and resource settings follow
the precedence below. Span content and hook options match `OpenTelemetryTracer`.

`harnessed.integrations.opentelemetry.otlp_provider(*, service_name=None,
endpoint=None, resource_attributes=None)` is an async context manager yielding an
SDK `TracerProvider` with batched OTLP/HTTP export. It requires `harnessed[otel]`.
Open once around all agents and sessions using the provider; exiting shuts down
only that provider outside the event loop, including on errors and cancellation.
It never installs a global provider. When supplied, `service_name` must contain non-whitespace
characters and overrides `service.name` in resources and environment variables.
`resource_attributes` accepts a mapping of OTel attribute values; explicit values
override resource environment attributes without mutating the mapping. `endpoint`
is a full trace ingestion URL; None uses exporter environment configuration.
Headers, timeouts and sampling remain configured through standard OTel settings.
Shutdown attempts to drain batches and does not confirm destination ingestion.

`harnessed.integrations.opentelemetry.OpenTelemetryTracer(*, tracer_provider=None,
capture_content=False, max_content_length=16384, max_image_bytes=5242880,
full_input_messages=False, content_filter=None, trace_hooks=False)` replaces the `tracer` slot and
requires `harnessed[otel]`. It emits nested runtime spans through the application's
OpenTelemetry provider, without owning its exporters or shutdown. Import the
integration explicitly; public SDK facades remain usable without the extra.
Pass an existing application provider directly when one is already configured;
the helper is optional. Content capture and limits stay on the plugin.
`capture_content` accepts a boolean or `ContentCapture`. Use
`ContentCapture.redacted(*secrets, include_exception_messages=False)` to capture
portable conversation and tool content while masking registered literal text
values, including their occurrences in subsequent model history. Each secret
is a string or a `SecretText` wrapper with `get_secret_value() -> str`, such as
Pydantic `SecretStr`. No settings framework is required. Values are snapshotted
at policy creation, omitted from its repr and rejected when empty or invalid.
Recreate the policy on credential rotation. Matching is case-sensitive and
covers substrings and object keys, including overlaps; encoded copies and
unknown secrets require application rules. Exception text requires the policy's
explicit opt-in. The boolean `True` retains plain content capture, including
exception text. Wrapped text secrets and Pydantic `SecretBytes` encountered
during portable conversion are masked in either mode.
`content_filter` is a synchronous `TraceContentFilter`, taking `TraceContent` and
returning `TraceContent | None`. These types and `TraceContentKind` are exported
by the optional integration. The callback receives a detached portable JSON value
before serialization or truncation, with its kind and optional tool name. It
covers turn input/output, model input/output, tool definitions, resolved settings,
output schemas, tool arguments/results and exception messages. Return
None to omit a field, or replace its value while preserving its identity;
exception messages must remain strings. Registered literals are masked after
the callback, including in replacement values, and before serialization and
limits. Filter and redaction failures omit the field, set a content-free marker
and preserve the agent's original outcome. Capture must be enabled; metadata
remains outside content filtering and redaction. `ContentCapture` and
`SecretText` are exported by the optional integration alongside the filter types.
Model spans export OpenInference `llm.input_messages`, `llm.output_messages`,
`llm.tools` and `llm.invocation_parameters` attributes instead of JSON blobs.
`max_content_length` limits each text part and tool-argument string, with an
explicit truncation marker. Each distinct image is exported whole on the first
model span of the session that includes it, up to `max_image_bytes`; later or
oversized occurrences become text placeholders. By default a model span lists
only messages that earlier model spans of the session did not show and records
the omitted count in `harnessed.input_messages.inherited`;
`full_input_messages=True` lists every request message. Filters see images as
digest stand-ins and can drop them but never read their data.
On turn and tool spans, each content field has its own serialized character
budget. Truncated content remains valid JSON and reports original length and
omitted-item counts after filtering/redaction. Turn summaries contain accepted
user input and the last assistant message, including validated structured output
and partial-result metadata.

`trace_hooks=True` opts into `harnessed.hook` spans for invoked listeners, with
identity metadata and inclusive Waterfall timing. Hook dispatch binds the
selected tracer even when this option is false; library work in hooks can use
the public facade. The optional standard-library-only kernel
`HookInstrumentation.invoke(scope, invocation)` boundary preserves dispatch
semantics and resolves from the dispatching scope. Automatic hook source-location
caching and duration filtering remain future work.
See [tracing](../guides/tracing.md) for Phoenix and Langfuse configuration,
content capture, parent context and the emitted attributes.

## MCP client

Install `harnessed[mcp]` and import `MCP` from `harnessed.plugins.mcp`.
The extra installs FastMCP's client dependencies. Harnessed consumes external MCP
servers; server hosting and exporting Harnessed tools over MCP are outside this SDK.
`MCP.http(name, *, url, headers=None, auth=None, ...)` connects over Streamable
HTTP through FastMCP. `auth` accepts a bearer-token string, `"oauth"` or an
`httpx.Auth` instance. `MCP.stdio(name, *, command, args=(), env=None, cwd=None, ...)`
connects to an installed stdio MCP server, managing its subprocess for the client.
Both accept `include_tools=None`, `readonly_tools=()`,
`startup_timeout_seconds=10.0`, `call_timeout_seconds=60.0`, `parallel=False` and `enabled=True`.
`MCP(name, *, transport: MCPTransport, ...)` accepts a custom transport recipe
implementing `create() -> fastmcp.client.transports.ClientTransport`.
Each mount owns one connection and a fixed catalog of namespaced tools. See
[MCP tools](../guides/mcp.md) for approvals, results and cancellation boundaries.

## Session tool inspection and plugin services

Plugin lifecycle methods use `PluginScope` from `harnessed.kernel`:
`async def setup(self, scope: PluginScope) -> None` and
`async def teardown(self, scope: PluginScope) -> None`. The same concrete view
shares the activation's service/event environment and cleanup stack. It exposes
resource acquisition, owned registrations, service publication, typed hooks and
explicit lookup, without composition or closure operations. Advanced session
integrations and standalone kernel plugins retain the full `Scope` and
`session.scope`. See the [activation contract](../guides/plugins.md#activation-scope-and-lifecycle-contract).
Model-visible input uses messages and `PrepareRequest` hooks; per-call services
use `ToolContext`.

`session.tools` is a live, read-only mapping. Use `session.tools.names` for a
`frozenset[str]`, `session.tools["read_file"]` for a visible `ToolDefinition`,
and `session.tools.values()` to iterate definitions. Iterating the mapping
itself yields names. Missing, disabled or inactive tools raise `KeyError` on
lookup. Existing views reflect activation, removal and replacement, using the
same visibility rules as model requests. Register tools through plugins.
`session.tools.resolve(selector)` returns the visible `ToolInfo` for an action
enum member or decorated `@tool` method, or `None`. `ToolSelector`,
`ToolReference` and `tool_name(method)` are exported by `harnessed.tools`.

Plugin authors import `SessionContext` from `harnessed.sessions`,
`SystemPromptStore` from `harnessed.prompts` and `ToolRegistry` from
`harnessed.tools`. `SessionContext` is the in-loop
session view carried by runtime hook events as `event.session`; it reads state,
appends messages, records observations, emits events and requests approvals, but cannot run, mount or close.
It is the only session service in a plugin scope; the application `Session` is
not injectable, and applications obtain the same view from `session.plugin_context`. Use `harnessed.tools.SessionTools` when annotating the read-only tool view.

Third-party decorators attach a `Registration` using
`harnessed.kernel.registration(recipe)(callback)`. This preserves the callback's
identity, signature and other declarations. Dependencies and activation-bound
methods follow the native registration lifecycle; recipes own cleanup through
the supplied scope. See [method declarations](../guides/plugins.md#third-party-method-declarations).

## Approval policy

Pass `approvals=Approvals(...)` to `Agent`. Omitting `allow` permits unmatched
tools by default. Supplying an allowlist makes the default denial, including for
read-only tools. `ask` and `deny` can restrict individual actions. `default`
sets an explicit fallback. Rules take `StrEnum` action keys and decorated methods.
MCP clients bind remote names to the same action keys using `actions=`.
Callbacks answer with `request.allow(remember=True)`, `request.allow()` or
`request.deny()`. The corresponding session operations accept a request ID.
Supply `reviewer=AutoReview(model=..., instructions=...)` for model review of
pending approvals. Custom reviewers implement `ApprovalReviewer` from
`harnessed.approvals`. A deferred review uses the existing approval responders.
See [approval configuration and response timeouts](../guides/approvals.md).

## Tool presentation

`@tool(presentation=ToolPresentation(...))` attaches
immutable display and JSON integration metadata to native tools. Import
`ToolAnnotations`, `ToolIcon` and `ToolPresentation` from `harnessed.tools`. Policy,
execution and review hooks expose the captured `ToolInfo.presentation`; display
values do not change model argument schemas or grant approval. See
[tool display and integration metadata](../guides/tools.md#tool-display-and-integration-metadata).

## Subagents

`Subagents(agents, *, max_parallel=3, max_calls=16, max_depth=1, timeout_seconds=180.0,
max_steps=8, token_budget=None)` from `harnessed.plugins` adds `spawn(agent, task)`,
`status(child_id, timeout_seconds=10.0)`, `send(child_id, message)`,
`wait(child_id, timeout_seconds=30.0)`, `cancel(child_id)` and `delegate(agent, task)`.
These tools are absent without the plugin. Values in `agents` implement
`ChildSessionFactory`, including `Agent`. `spawn` returns after admission;
`delegate` waits for a receipt. `status` asks the child for an explicit report,
coalesces pending requests and forwards late answers through the parent inbox.
The default depth is one; children cannot raise an inherited limit.
See [subagents](../guides/subagents.md) for approvals, limits,
recovery and ownership.

## Workspace providers and tools

Import `LocalWorkspace`, `WorkspaceBackends`, `Filesystem` and `Shell` from
`harnessed.plugins`. Local agents use `[LocalWorkspace(root=path), Filesystem(), Shell()]`.
To bring an existing file store use `[WorkspaceBackends(filesystem=storage), Filesystem()]`.
A provider exposes services. Tool plugins declare which operations reach the model.
A missing required backend fails default session startup validation.

## Sandbox integrations

Import `DockerSandbox` from `harnessed.integrations.docker`, or
`BubblewrapSandbox` from `harnessed.integrations.bubblewrap`. Both own shared
shell/filesystem backends; compose them with `Shell()` and `Filesystem()`.
Docker requires an explicit `image`; Bubblewrap requires a dedicated `rootfs`.
Both accept `workspace=Workspace.copy_from(path)` or `Workspace.mount(path,
readonly=True)`, `network=NetworkAccess.NONE`, explicit `env`, `SandboxLimits`,
`max_output_bytes` and `startup_timeout_seconds`. No extra Python dependencies are required;
their native runtimes must be installed separately. Docker also accepts `runtime`
for an application-configured OCI runtime.

`await sandbox.export(session, source=..., destination=...)` exports one bounded
binary file while that recipe's session mount is live. `sandbox.services(session)`
returns typed `SandboxServices`: assign `shell = services.shell` and
`file_system = services.file_system` for direct `shell.run(...)`,
`file_system.read(...)`, `file_system.read_bytes(...)` and
`file_system.grep(query, pattern="**/*", limit=100)` calls. Grep returns
`SearchResult(matches, scanned_files, notes)` and executes in one built-in worker
request. Text ranges count Unicode characters; a range that exceeds the transfer
byte budget raises `ValueError`. Invalid UTF-8 raises `UnicodeError`.
`Shell` accepts only `cwd`, `timeout_seconds` and `max_timeout_seconds`; configure `env` and
`max_output_bytes` on the sandbox/backend. Shell deadlines include queueing and
launcher startup, and expired sandbox startup raises `TimeoutError`.
`Sandbox(backend=MyBackend, workspace=...)` accepts a zero-argument constructor or
factory returning `SandboxBackend`. Its asynchronous `start()` guarantees readiness,
its `shell` and `file_system` properties expose the existing typed backend contracts,
and `aclose()` owns cleanup. Worker transport remains internal to integrations. See
[sandboxes](../guides/sandboxes.md) for defaults, lifecycle, security boundaries,
runtime requirements and the extension protocol.

## Background shell commands

Import `BackgroundShell` from `harnessed.plugins` and register it explicitly with
`LocalWorkspace(root=...)`, a sandbox, or `WorkspaceBackends(shell=backend)` in `Agent(plugins=...)`.
`BackgroundShell(cwd=".", *, timeout_seconds=600.0, max_timeout_seconds=600.0,
max_background_commands=32)` requires an existing shell backend and a live session.
It adds `start_command(argv, timeout_seconds=None)`, `read_command(command_id, wait_seconds=0.0)`,
`write_command(command_id, text="", *, eof=False)`, `terminate_command(command_id)` and
`list_commands()`. `Shell` exposes the foreground command tool.

`start_command` returns a mount-scoped ID promptly. Completion, failure, timeout
or cancellation queues one durable session-inbox notification and wakes an idle
agent. Output stays behind the normal `read_command` tool pipeline. Process trees
are cleaned up on termination, plugin disposal, backend replacement and session
closure; live handles are not restored or replayed. Backend capture limits and
the 600-second maximum deadline still apply.

`BackgroundShellBackend`, `ShellProcess`, `ProcessSnapshot`, `ProcessState`,
`CommandInfo` and `CommandResult` are exported from `harnessed.plugins.shell`.
Run-only backends explicitly reject background starts. See
[registration, tool sequences and lifecycle](../guides/background-shell.md).

## Agent

`Agent(*, model=None, instructions=None, approvals=None, context=None, plugins=(), loop=None,
plugin_validation=PluginValidation.ERROR)` resolves reusable configuration.

| Member | Behavior |
| --- | --- |
| model | Harnessed Model or provider:model string; wrap native models with PydanticAIModel |
| instructions | Text prepended to plugin instructions, a fixed SystemPrompt, or a SystemPrompt.from_plugins collection strategy |
| approvals | Application approval rules, defaulting to allow |
| context | Context window, tool output character limit and optional history compaction |
| plugins | Iterable of Plugin instances, mounted in order; duplicate slots raise PluginConflict |
| loop | AgentLoop value with step limits, tool execution and output retries; defaults to `AgentLoop()` |
| plugin_validation | ERROR rejects unresolved plugins at session startup; WARN emits PluginDependencyWarning and retains them for later activation |
| agent.plugins | Tuple of configured plugins |
| agent.loop | The configured AgentLoop |
| agent.describe() | Immutable CompositionDescription with the configured plugins in order |
| agent.prompt | Inspectable PromptComposition with final order, origins and format |
| agent.session(*, state=None, id=None, on_commit=None, commit_timeout_seconds=None, restore_outputs=()) | Open a Session, optionally from a SessionState or SessionCheckpoint; on_commit receives its commits inside log admission and must not write to or flush its own session; commit_timeout_seconds bounds each delivery and fails it with CommitTimeout, shared by subagent sessions |
| agent.run(content, state=None, **options) | Run one turn from text or a Message and return its terminal result and state |
| agent.child_session(request) | Factory boundary for a fresh parent-owned child; application code normally uses Subagents |
| SessionState.from_json(payload) / state.to_json() | Validate or encode the canonical conversation snapshot in memory |
| SessionState.from_dict(document) / state.to_dict() | The same snapshot as a JSON-compatible dict, for example a JSONB column |

`SessionCommit`, `SessionChange`, `SessionCheckpoint` and `CommitHandler`
(`Callable[[SessionCommit], Awaitable[None]]`) are exported from `harnessed.sessions`.
Harnessed stores nothing itself. `SessionCommit(checkpoint, changes)`
carries the complete recovery checkpoint, whose `session_id` is also
`commit.session_id`, and the ordered
`SessionChange(sequence, event)` values since the previous delivered commit.
Map domain changes and the checkpoint in one application-owned transaction. Old
changes are not emitted again on restore. Token deltas are excluded.
Commits are awaited inline at input, model/tool intent, result, pause, turn-end
and custom durable event barriers; other durable events travel with the next
barrier, `session.flush()` or close. Child sessions deliver through their parent's
handler. A handler exception reaches run/result/stream consumers and rejects
later admissions; reopen from the last stored checkpoint.
`session.checkpoint()` captures the same value on demand, including an open turn.
Checkpoints are frozen Pydantic dataclasses with typed state, execution events,
controls and resources. `to_dict()` / `from_dict()` and `to_json()` /
`from_json()` provide canonical encoding without requiring a plugin catalog.
`CheckpointExtension` preserves application-defined event data for validation by
the owning session. The application owns pool lifetime and coordinates active
workers per ID.

Closing a paused session with a commit handler delivers
its execution; reopening it remains paused. Explicit `SessionState` values still
contain terminal conversation state only. See [sessions](../guides/sessions.md)
for recovery, output-validator restoration and checkpoint format boundaries.

Import `PluginValidation` from `harnessed`. Session startup checks all waiting
plugins after publishing session services and settling their setup, before
starting the runtime or resuming queued work. By default it raises
`PluginDependencyError`, a `ConfigError` from `harnessed.errors`. Its `.issues`
tuple contains immutable `PluginDependencyIssue` snapshots with `.plugin`
(`PluginSpec`) and `.missing` (service keys, preserving their identity).
`PluginValidation.WARN` emits one `PluginDependencyWarning` with the same `.issues`
and permits later activation. Setup failures still propagate in either mode.
See [startup validation](../guides/plugins.md#session-startup-validation).

## Composition

Compose an agent with a model, instructions, loop, context, approvals and
an ordered list of `Plugin` instances. Two plugins claiming the same slot raise
`PluginConflict`. Return plain lists from composition functions and include
conditional features when building the list. `agent.describe()` exposes the
configured model and loop and a tuple of plugin descriptions with their names,
slots and declared dependencies. The runtime mounts this configuration for each
session. See [plugin composition](../guides/plugins.md).

Start with `Agent(model="openai-responses:gpt-6.1-terra")` to select a model.
Provider strings automatically select the PydanticAI adapter. Use `PydanticAIModel` from `harnessed.models.pydantic_ai` for generation defaults
or adapter options; wrap a native
PydanticAI model when configuring its provider or client. See
[model configuration](../guides/models.md#choose-a-model).

Model generation defaults live on `model.settings: GenerationSettings`, exported
from `harnessed.models`. Configure `PydanticAIModel(..., settings=GenerationSettings(...))`
and optionally override concrete values with `session.run(..., settings=...)`.
`max_tokens` and `temperature` are typed fields; JSON provider options use
`provider_options`. None inherits a default. The effective request snapshot stores
these settings once, and `ModelRequestedEvent.model` records the selected
`ModelInfo`. See [model settings and ownership](../guides/models.md#settings-and-ownership).

## System prompt composition

`Plugin.instructions() -> SystemPrompt` is the single plugin instruction contract.
The base method returns an empty document. Overrides synchronously derive actual
content from recipe configuration, without services or activation state.

`SystemPrompt(*parts, format=PromptFormat.PLAIN)` is an immutable content document.
Parts are strings, `Instructions`, nested documents or `None`; `None` is normalized
away. The outermost format controls all nested content. PLAIN joins text with blank
lines, MARKDOWN adds optional level-two headings, and XML escapes section content.

`SystemPrompt.from_plugins(*parts, format=...)` collects plugin blocks; it is
also the default when `Agent(instructions=)` is omitted. Fixed parts appear
before complete plugin blocks in plugin-list order. Providers run once at agent
construction.
A plugin provider returns a content document and cannot request collection itself.
A prompt built with the `SystemPrompt` constructor omits plugin blocks without
calling their providers; `str(agent.prompt)` lists the omitted plugins.
Collection strategies cannot be nested inside content.

`Agent(instructions="...")` prepends application text and collects plugin blocks.
Supply a `SystemPrompt` to select a complete document or collection strategy.

`Instructions(text="", title=None, description="")` is a content value, not a
plugin descriptor. `from_file(path=..., title=..., missing_ok=False)` and
`from_factory(factory=..., title=..., description=...)` defer reads or async
callbacks taking `PluginScope` until each session opens. Factory registrations
and cleanup have session ownership; ordinary plugin lifecycle registrations have
activation ownership. Passing an existing kernel `Scope` to `render()` supplies
its stable `scope.plugin_scope` view to the factory.
`print(agent.prompt)` shows final order and origins;
`await session.prompt.render()` returns the actual session text.

`async with session.mount(plugin, section_delivery=SectionDelivery.ERROR)` rejects overridden
instruction providers before mounting. `CONTEXT` evaluates the method and content
sources once per activation and delivers mount-owned context, removed on suspension
or unmount. Failures roll back the mount. `IGNORE` never calls providers, factories
or reads files. Runtime delivery does not change the fixed system prompt.
See the [context guide](../guides/context.md).

Use `await session.mount(plugin, section_delivery=...)` for a manually managed
handle, and close it with `await handle.aclose()`.

## Session

`session.children` exposes immutable child snapshots; `session.parent` is the
child's `SubagentLinkedEvent` or `None`. `session.usage` remains local;
`session.tree_usage` adds recorded child receipts recursively. These inspection
properties are also available on `SessionLog`. Running or interrupted children
may have incomplete recorded usage.

`session.inbox.steer(content, during_run=handle.id)` admits guidance only while
that submitted run is queued or open. The check is atomic with inbox admission;
after completion it raises `SessionStateError` instead of starting a new turn.

| Member | Behavior |
| --- | --- |
| run(content, output=None, attachments=(), settings=None, max_steps=None, token_budget=None) | Queue and await this turn's RunResult[T]; content is text or a UserMessage, as on Agent.run() |
| submit(content, output=None, ...) | Acknowledge queued input and return RunHandle[T] |
| stream(content, output=None, ...) | Async context manager yielding RunStream[T]; iterate it for UI and custom events, ending with TurnResultEvent |
| steer(content, attachments=()) | Send a next-step correction and return its InboxItem |
| observe(message: UserMessage, source=..., refs=()) | Buffer the latest text/image snapshot per source for the next step; recorded snapshots stay unchanged until compaction or an explicit retention policy |
| append_message(message: UserMessage) | Append a user message for subsequent model requests without starting a turn or invoking the model; commit before returning when a store is configured |
| inbox.pending / remove(id) | Frozen in-memory queued input / admitted removal |
| pause() / resume() | Pause at a committed step boundary / continue |
| stop(keep_inbox=False) | Finish this step, end its turn, return to idle |
| abort(keep_inbox=False) | Cancel now, drain cleanup, repair and end its turn |
| wait_until_idle() | Wait for idle and an empty inbox |
| mount(plugin, section_delivery=SectionDelivery.CONTEXT) | Use with async with for a temporary mount, or await a handle and call handle.aclose() |
| compact() | Summarize completed history while idle or paused; return its record or None |
| snapshot() | Export immutable conversation state at an idle boundary |
| flush() | Await pending checkpoint persistence; no I/O without a store |
| subscribe(transient=False) | Independent raw log subscription |
| events / turns | Current-turn execution buffers, released after terminal delivery |
| model_request_count | Number of local `ModelRequestedEvent` records across turns, including failed and cancelled requests. Provider transport retries share one request. On reopen, only requests in a restored in-progress turn carry over. |
| messages / usage / last(Event) | Retained working context, live-session usage and recent diagnostics |
| status | SessionStatus: IDLE, RUNNING, PAUSED, STOPPING, CLOSED |
| emit(event) | Admit a declared execution event or publish a transient notification |
| `approvals.request(call, reason)` | Ask for approval in the live session |
| `approvals.request_response(call, reason)` | Ask for approval and return the answer with its source and optional rationale |
| `approvals.allow(id, remember=False)` / `approvals.deny(id)` | Answer a pending approval. Set `remember=True` to grant future calls for the same action and scope |
| `approvals.revoke(id)` | Revoke a remembered grant. Use `pending`, `grants` and `grant_for()` to inspect approvals |
| `async with approvals.responder():` | Keep questions of turns without an owning stream pending and announce them as `ApprovalNeededEvent` on `subscribe(transient=True)`; a closing stream hands its questions over |
| id / prompt | Session ID / effective system prompt |
| tools | Read-only `SessionTools` mapping of currently visible tool names to definitions; `.names` returns a frozenset |
| plugin_context | The restricted `SessionContext` view that plugins and hooks receive |
| scope | Advanced: resolve services in this session's scope (`session.scope[ToolRegistry]`); import container types from `harnessed.kernel` |
| control | Advanced: the SessionControl service for plugins and external UIs |
| log | Advanced: live SessionLog state owner, current-turn buffer and event catalog |

`from harnessed import SessionLog` exposes the injectable log service for plugins.
Applications read through the session views above. `session.log` exposes the same
data for plugins and integrations; prefer the session views when both exist.
`SessionLog.request(request_id)` returns a submitted model request or `None`;
`request_finished(request_id)` checks completion within the current turn, and
`execution_pending(call_id)` checks whether a tool start still needs a receipt.
`included_observations(request)` returns retained observation sources, refs,
turn IDs, step numbers and message IDs using exact message provenance rather
than prompt text searches. See [automatic observations](../guides/context.md#steering-observations-and-retrieval).

`observe(UserMessage(content), source=..., refs=())` accepts text or a tuple of
`TextPart` and `ImagePart` values in the message. A `PreStep` hook supplies snapshots
automatically; no model tool call is required. Text and images
share one recorded message and lifecycle. `LatestObservationOnly()` explicitly
keeps only the latest snapshot per source; otherwise snapshots remain unchanged
until compaction. `Compaction(keep_last_steps=5, ...)` retains five completed steps across turn
boundaries, including their full observations, plus unfinished/current context.

Await methods that perform durable writes. Closed handles raise SessionClosed.
SDK failures derive from `HarnessedError`, importable from `harnessed.errors`: operations
invalid in the current status raise `SessionStateError` (SessionClosed is one),
corrupt stored records raise `SessionFormatError`, an `on_commit` delivery that
exceeds `commit_timeout_seconds` raises `CommitTimeout`, and `on_max_tokens=FAIL`
raises `OutputTokenLimit`.
See [session boundaries and restart behavior](../guides/sessions.md).

Use `AgentLoop` for step limits, parallelism, tool timeouts and output retries.
Use `Context` for input tokens, output reserve, tool result length and compaction.
Per-run `max_steps=` and `token_budget=` narrow execution for a single turn.

`AgentLoop(final_step=FinalStepPolicy.SUMMARIZE)` reserves the last available
step for an action-free text summary. Import `FinalStepPolicy` from
`harnessed.plugins`; the default is `NONE`. The summary returns `MAX_STEPS` with
`completed=False`, respects per-run limits and produces only `result.text`, even
when the run requested structured output. See [reserved summaries](../guides/sessions.md#step-progress-and-a-reserved-summary).

`ModelRequest.append_message(message)` returns an extended request without
mutating it or adding to conversation history. Transform requests through
`PrepareRequest`; `ConfigureRequest` is removed. There is no `context=` run option,
`ContextValue` helper, or public `DynamicContext` service.

## RunHandle and RunStream

Import both from `harnessed`. `await session.submit(...)` returns a
`RunHandle[T]` for one queued, executing or completed run. The session owns its
execution; the handle provides its identity, result and targeted cancellation.
`async with session.stream(...) as run` yields a `RunStream[T]`, a specialized
`RunHandle[T]` with live event observation installed before input admission.

| Member | Behavior |
| --- | --- |
| id | Identity of this run's submitted input |
| done() | Whether this run has completed, independently of observation |
| result() | Await the same `RunResult[T]` repeatedly; cancelling a waiter leaves execution intact |
| cancel() | Discard this queued run or abort its active turn, preserving other queued runs; completed runs are unaffected |
| async iteration (`RunStream` only) | Consume `StreamItem[T]` values once; the final event on normal exhaustion is `TurnResultEvent[T]` |
| aclose() (`RunStream` only) | Close event observation without cancelling the run |

`RunHandle` has no async iteration or `aclose()` method. A `RunStream` can be
passed anywhere a `RunHandle` is accepted. One consumer owns its event iterator;
an exhausted or closed iterator does not replay events. Leaving the stream
context closes observation and its approval responder, while `result()` and
`cancel()` remain available. Closing the session fails unfinished runs with
`SessionClosed`. See [streaming](../guides/streaming.md) for delivery and error
semantics.

## RunResult and values

`harnessed.models` exports `SystemMessage`, `UserMessage`, `AssistantMessage` and
`ToolMessage`, the four frozen variants of `Message`. User and tool messages can
contain text and image parts. Role names describe provider messages, not which
person or plugin authored them. `ToolResult` is a tool's return value; a
`ToolMessage` is its corresponding message in model history.

A result belongs to one turn. It provides `text`, `events`, `messages`, `usage`,
`turn_id`, typed `stop_reason` and `(ToolCall, ToolResult)` pairs in `tool_calls`.
`completed` is true only for `StopReason.END`; check it before trusting `text`,
because limits, stops and discards return a result instead of raising.
Passing `output=T` or `output=OutputContract("answer.v1", T)` adds a validated
`output` of type `T`; an unstructured result's
output is None. See [structured answers](../guides/structured-output.md) for retry
limits, incomplete turns and restoration after a process restart.
`PrepareOutput` from `harnessed.hooks` lets a session-scoped plugin return
`OutputDecision.DEFER` for working steps and compose with `next_()` to enforce the
declared contract after completion. It runs after `PrepareRequest`, before output
constraints and token checks. Both native JSON and the reserved answer tool are
deferred; the effective request schema also controls validation. The turn's
declared output identity and ordinary correction limits remain unchanged.
`result.require_output()` returns that original value only after successful turn
completion. It raises `OutputUnavailable` with the stop reason for incomplete work.
For completed unstructured runs it returns None. The `output` property remains
available to inspect a validated value without asserting completion.
The [UI stream](../guides/streaming.md) carries text/reasoning deltas, tool lifecycle,
approval requests, status and one terminal result; raw log subscriptions are separate.
StopReason includes END, MAX_TOKENS, MAX_STEPS, TOKEN_BUDGET, STOPPED, ABORTED and DISCARDED.

`ModelUsage` keeps input/output, cache-read/write, optional reasoning tokens and
optional cost separate. Unknown optional counters remain unknown when combined.
Frozen values can be compared structurally; mapping values are deliberately
unhashable. See [values and record versions](../concepts/values.md).

## Plugin authoring

| API | Purpose |
| --- | --- |
| Plugin / @tool | Class-based reusable capability |
| @tool(description="...") | Explicit model-facing description; None falls back to the docstring |
| @tool(enabled=True \| False \| predicate) | Per-tool visibility and execution condition; synchronous predicates use Inject and mounted instance state |
| Annotated[T, ToolParameter(enabled=True \| False \| predicate)] | Per-activation parameter visibility and acceptance; requires a default, supports signature-derived arguments |
| @tool(metadata=(value, ...)) | Immutable application annotations keyed by exact type; no automatic model or persistence effect |
| @tool(chain=ToolChain.STOP_AFTER) | Run this tool, then skip remaining calls from the same model response; default CONTINUE |
| ToolResult(..., break_chain=True) | Request the same boundary dynamically; already-started parallel calls finish |
| Plugin[MyConfig] / config=MyConfig(...) | One frozen module-level model supplies runtime validation and the type of self.config |
| PluginState(factory) | Typed state freshly created before each mount's setup; available through cleanup |
| SessionValue(schema, key=..., default=...) | Typed conversation value persisted under a stable key; `get()`, `await set(value)`, `await update(change)`. From `harnessed.sessions` |
| name = needs(T) | Stable typed service binding and lifetime requirement; no duplicate needs entry |
| name = observes(T) | Nonblocking service-or-None binding; provider changes restart the activation |
| class Capability(Plugin, provides=True) | Publish the mount under the declaring class; provides=Key selects another key |
| Plugin.create_instance() | Customize fresh instance construction; defaults to replaying explicit constructor arguments |
| Plugin.with_instructions(str \| SystemPrompt) | Return a recipe with a replacement prompt block. An empty SystemPrompt removes the block |
| Plugin.with_tool(method, *, description=..., parameters=...) | Return a recipe copy with a configured tool description and model-facing fields. `harnessed.tools.Param` accepts `description`, `default`, `ge` and `le` |
| Plugin.without_tools(*methods) | Return a recipe copy that skips registration of the selected `@tool` methods |
| Plugin.without_instructions() | Return a recipe that contributes no prompt block |
| Plugin.setup(scope: PluginScope) / teardown(scope: PluginScope) | Async activation lifecycle; dependencies bind first and cleanup remains framework-owned |
| PluginScope | Concrete resource/registration view; enter, effect, provide, on, dispatch, register, lookup and owner_name |
| class Capability(Plugin, needs=(Backend,)) / Inject[T] | Temporal lifetime / per-call service resolution |
| @hook / Scope.hook | Typed observer, bail or waterfall extension |
| @hook(EventA, EventB, ...) | One handler accepting every listed event; shared priority and prepend options |
| ToolContext | Call, log, signal, cancelled, progress, emit and approve(reason) |
| scope.register(install) / RegistrationState.active | Owned registration and lifecycle visibility |
| RegistrationState.activate() | Invoke an integration callback in its registration owner's ambient context |
| scope.effect(cleanup) | Owned idempotent sync/async cleanup |
| await scope.enter(manager) | Async context manager exited when the scope closes |
| Service(T, implementation) | Structural dependency registration |
| mount_plugin(recipe, services=...) | Typed test activation with cleanup; exported by harnessed.testing |

`class Files(Plugin[FilesConfig]): ...` inherits a typed constructor requiring
`config=FilesConfig(...)`; the model is validated when it is constructed. Missing,
non-model, mutable or mismatched configurations fail before activation. The
`Config = ...` alias and arbitrary keyword forwarding were removed. Plain
`class Observer(Plugin): ...` needs no config argument. Explicit feature constructors
may build the model internally while preserving their own typed signatures.
See [typed configuration](../guides/plugins.md#typed-configuration-without-duplicate-declarations).

Tool parameters supplied by the framework require `Inject[T]`, including
`Inject[ToolContext]`. An unmarked `ToolContext` is rejected at registration.
Declare stable service bindings with `needs` and `observes` from `harnessed.kernel`; add
`needs=(...)` for lifetime requirements without an attribute. Optional fields are
reported as `observes`. Class-level `Inject` fields were removed. Callable `Inject`
parameters bind values without adding lifetime dependencies.
Both fields are `ServiceView` descriptors. Subclass `ServiceView[S, V]` and
override `present(service)` to expose a required service as a typed view, as
`SessionValue` does; the binding remains a lifetime requirement of the plugin.
Higher-level `Plugin.setup()` and `teardown()` require service fields instead of
`Inject` parameters; standalone kernel lifecycle injection remains available.
`session.tools.explain(name)` returns a frozen `ToolExplanation` distinguishing
available, missing-service, disabled, excluded and not-registered states through
`ToolAvailability`. Both types are exported by `harnessed.tools`; the existing
tool mapping continues to contain only model-visible definitions.
Model-filled parameters become a strict Pydantic form; unknown fields and invalid
values fail before invocation. `@tool(params=MyParams)` uses a Pydantic `BaseModel`
subclass as the top-level argument schema and passes its validated instance to the
method's single non-injected parameter. Injected parameters remain separate.
See [parameter objects](../guides/tools.md#pydantic-parameter-objects),
[tools and hooks](../guides/tools.md) and
[composition](../guides/plugins.md).

`ToolPolicy`, `ExecuteTool` and `ReviewToolResult` expose the captured
`ToolInfo | None` as `event.tool`; `event.tool.metadata.get(T)` returns `T | None`.
`event.matches(selector)` and `ToolInfo.matches(selector)` compare the captured
registration with an action enum member or decorated `@tool` method.
`ToolChain`, `ToolInfo` and `ToolMetadata` are exported by `harnessed.tools`.
The same hooks expose per-call `event.envelope: ToolEnvelope`. `@tool(envelope=(M,))`
merges Pydantic model fields into a tool's schema, validates them and removes them
before the callback; `ToolEnvelope.get(M)` returns `M | None`.
`CallEnvelope(M, *, select=None, on_invalid=InvalidEnvelope.DROP,
position=EnvelopePosition.LEADING)` from
`harnessed.plugins` adds `M` to every selected tool, including other plugins',
MCP and external tools, for the plugin's lifetime; it builds on
`ToolRegistry.envelope(M, *, select, on_invalid, position, owner)`. `TRAILING`
places the fields after the tool's parameters instead of before them. `envelope_value(call, M)`
from `harnessed` reads `M | None` from a call's raw arguments without raising.
See [tool call envelopes](../guides/tools.md#tool-call-envelopes) and
[envelopes for every tool](../guides/tools.md#envelopes-for-every-tool).
Skipped calls have `ToolStatus.SKIPPED` and retain their model-history pairing.
`PreStep.progress` and `PrepareRequest.progress` are a frozen `TurnProgress`
(`turn_id`, `step`, `max_steps`, `final_step`). `max_steps` is the effective turn
limit; `remaining_steps` includes the current step, `is_last_step` indicates one
remaining step, `is_final_step` identifies an action-free reserved summary, and
`last_tool_step` is the last step that offers tools.

`PreStep` runs in the harness before the model call. Its `StepDecision` accepts
or filters queued input messages; `StepDecision.reject()` ends the turn without
calling the model. This is a plugin decision, not an LLM response. Supply automatic
environment snapshots through `session.observe()` so compaction can treat them
as step history rather than protected user instructions.

`StreamModel` from `harnessed.hooks` is a
`Waterfall[AbstractAsyncContextManager[ModelStream]]` around the agent loop's model
transport. It exposes `session: SessionContext`, the transport-ready
`request: ModelRequest`, selected `model: Model` and `request_id: str`. Return an
unentered context manager to wrap or replace the default `model.stream(request)`.
It runs after request admission, budget checks and image resolution; use
`PrepareRequest` for request changes. The runtime retains stream consumption,
usage, diagnostics and assistant/tool event handling. Standalone model calls and
compaction requests bypass this hook. See
[stream middleware](../guides/models.md#wrap-or-replace-an-agent-model-stream).

## Event classes and data

`harnessed.events` exposes `TextDeltaEvent`, `ReasoningDeltaEvent`, `ToolCallRequestedEvent`,
`ToolCallGenerationStartedEvent`, `ToolArgumentsDeltaEvent`, `ToolCallFinishedEvent`, `ApprovalNeededEvent`,
`CompactionAppliedEvent` and `SpillCreatedEvent`, among other
classes. Import these full names directly; they are the canonical class names,
also used by `@hook`. `TurnEndedEvent` is the durable turn-end record;
`TurnResultEvent[T]` from `harnessed.events.stream` carries the assembled result at the end of a UI stream.
Every event has an explicit namespaced `type` discriminator.
See [events and schemas](../guides/events.md).
The [event reference](events.md) lists every built-in event, its discriminator
and whether it is part of the UI stream or only the durable audit log.

From `harnessed.events.stream`, `StreamEvent[T]` describes the built-in UI union and `StreamItem[T]` includes the
application extension bases. Declare frozen dataclasses extending `DurableEvent`
or `TransientEvent` and list them on `Plugin.events`. `EventRegistry` from `harnessed.events.registry` supplies
session-local validation, durable schemas and encode/decode operations.

### Stored images

`harnessed.models` exports `AssetRef` and `ImageReference` alongside `ImagePart`.
`ImageReference(asset, detail=ImageDetail.AUTO)` is a content part usable in user
messages, tool results and observations. `session.assets.put(data, media_type=...)`
stores bytes and returns an `AssetRef`; `session.assets.read(ref)` returns verified
bytes. Inline image data URLs are stored automatically on standard event admission.
Use `await session.log.resolve_request(request)` when replaying a recorded request
directly through a model; the agent loop resolves images automatically.
See [session image storage](../guides/sessions.md#image-assets-and-memory) for
cache bounds, self-contained snapshots and ownership.

The image API has three explicit boundaries:

| Audience | Public contract |
| --- | --- |
| Applications and plugins | `AssetRef`, `ImageReference`, `session.assets.put(...)`, `session.assets.read(...)` |
| Manual replay and inspection | `session.log.assets`, `await session.log.resolve_request(request)` |
| Application persistence | `SessionState.from_json(payload)`, `result.state`, `session.snapshot()`, `state.to_json()` |

`session.assets` and `log.assets` are read-only references to the same backend;
applications can store/read content but cannot replace session storage in place.
`AssetRef.verify(data)` raises `ValueError` on a size or digest mismatch. Snapshots
carry verified image bytes and referenced spill contents. A later live session
creates its own resource containers. Cache controls and concrete memory stores
are internal; the application owns storage of the exported snapshot.
`File` and `Image` are attachment descriptors supplied to `run`/`steer`; their
conversion methods are internal too. Use the public operations above rather than
reaching into those implementations.

## SessionState

`SessionState` is exported from `harnessed.sessions`. `state=None` starts a conversation
without history. `SessionState.from_json(str_or_bytes)` validates format version
4 and resource integrity; `state.to_json()` returns a string without I/O. State
contains retained history and provenance, plugin session values, compaction metadata,
parent/child references and terminal child receipts, and
referenced image/text resources. It contains no runtime handles, queued turns,
Python validators, approval grants or historical request/event archive.

`RunResult.state` captures that result's terminal boundary independently of later
turns. Imported state opens idle and never re-executes recorded tool calls.
`session.snapshot()` requires an idle open conversation with an empty inbox.
Reuse the same immutable state for independent continuations. See
[the session guide](../guides/sessions.md) for compaction and application storage.


## Compaction policy and services

`ContextMeter` from `harnessed.context` is a session service. Plugins declare
`needs(ContextMeter)` and call `await meter.measure(request)` to get the
`ContextState` (input tokens, reserved output, effective window, turns since
compaction, `ratio`) that compaction triggers receive.

`Context(compaction=Compaction(...))` enables summarization. `Compaction` accepts these keyword arguments:

| Argument | Default | Contract |
| --- | --- | --- |
| `at` / `fallback_tokens` | `0.8` / `None` | Ratio threshold and optional absolute trigger for unknown windows. |
| `trigger` | ratio threshold | Synchronous `ContextState -> bool`; trigger ratio includes reserved output. |
| `llm` | `None` | Harnessed `Model` or model name; omission captures the current session model. |
| `prompt` | `None` | Complete system prompt, or sync/async `CompactionContext -> str` factory; omission uses `DEFAULT_COMPACTION_PROMPT`. |
| `summarizer` | `None` | Custom `Summarizer`; mutually exclusive with explicit `llm` and `prompt`. |
| `keep_last_steps` | `2` | Positive count of completed model/tool steps retained across turns. |
| `target_ratio` | `None` | Finite fraction in `(0, 1]` for the whole input after compaction; requires a known window. |
| `target_tokens` | `None` | Positive absolute whole-input target; mutually exclusive with `target_ratio`. |
| `max_summary_tokens` | `None` | Positive strict summary-text ceiling, measured by the session counter. |

`CompactionPrompt` and `DEFAULT_COMPACTION_PROMPT` are exported by `harnessed.context.compaction`.
The same module exports frozen `CompactionContext`, `CompactionRequest`,
`Summary` and the structural `Summarizer` protocol:

```python
async def summarize(self, request: CompactionRequest) -> Summary: ...
```

`CompactionContext` contains `history_to_compact`, `retained_context`, `instructions`,
`state` and resolved `target_tokens`. `CompactionRequest.max_summary_tokens` is the
runtime's finite text budget after reserving space for any plugin notes.
Results must be complete `Summary(text, usage=None)`
values; output is never truncated to fit. One further summarization attempt is
permitted. Targets are best effort; hard request budgets remain mandatory.

Plugins use `PrepareCompaction` from `harnessed.hooks` to contribute optional
summary prefixes with `event.add_note(key=..., text=..., min_summary_tokens=1)`.
`CompactionAppliedEvent.accepted_note_keys` reports which contributions were
committed. See the [compaction guide](../guides/compaction.md#add-a-plugin-handover-note)
for budgeting and lifecycle.
