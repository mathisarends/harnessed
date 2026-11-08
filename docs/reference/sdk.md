# SDK reference

This page describes the implemented application API. Detailed behavior and complete
examples live in the topic guides. Optional features are imported from
`harnessed.plugins`; model contracts and message types come from `harnessed.models`,
and SDK errors from `harnessed.errors`.

## Tracing

`harnessed.integrations.opentelemetry.otlp_provider(*, service_name,
endpoint=None, resource_attributes=None)` is an async context manager yielding an
SDK `TracerProvider` with batched OTLP/HTTP export. It requires `harnessed[otel]`.
Open once around all agents and sessions using the provider; exiting shuts down
only that provider outside the event loop, including on errors and cancellation.
It never installs a global provider. `service_name` must contain non-whitespace
characters and overrides `service.name` in resources and environment variables.
`resource_attributes` accepts a mapping of OTel attribute values; explicit values
override resource environment attributes without mutating the mapping. `endpoint`
is a full trace ingestion URL; None uses exporter environment configuration.
Headers, timeouts and sampling remain configured through standard OTel settings.
Shutdown attempts to drain batches and does not confirm destination ingestion.

`harnessed.integrations.opentelemetry.OpenTelemetryTracer(*, tracer_provider=None,
capture_content=False, max_content_length=16384, content_filter=None)` replaces the `tracer` slot and
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
covers model input/output, tool arguments/results and exception messages. Return
None to omit a field, or replace its value while preserving its identity;
exception messages must remain strings. Registered literals are masked after
the callback, including in replacement values, and before serialization and
limits. Filter and redaction failures omit the field, set a content-free marker
and preserve the agent's original outcome. Capture must be enabled; metadata
remains outside content filtering and redaction. `ContentCapture` and
`SecretText` are exported by the optional integration alongside the filter types.
See [tracing](../guides/tracing.md) for Phoenix and Langfuse configuration,
content capture, parent context and the emitted attributes.

## PluginPack resolution

`PluginPack.resolve(*, prompt=PluginPackPromptPolicy.INCLUDE) -> ResolvedPluginPack` resolves one pack
without agent defaults, plugin mounts or instruction rendering. Import these
types from `harnessed`. The frozen result exposes a flat `plugins` tuple and a
`CompositionDescription` in `description`, preserving original origins,
replacements and exclusions. `PluginPackPromptPolicy.OMIT` recursively skips `PluginPack.prompt`
declarations before conflict resolution; explicit prompt plugins and selected
plugins' instruction contributions remain. Flattened plugins become direct
inputs when passed to an Agent. See [pack resolution](../guides/plugins.md#resolve-a-pack-into-flat-recipes).

## MCP client

Install `harnessed[mcp]` and import `MCP` from `harnessed.plugins.mcp`.
The extra installs FastMCP's client dependencies. Harnessed consumes external MCP
servers; server hosting and exporting Harnessed tools over MCP are outside this SDK.
`MCP.http(name, *, url, headers=None, auth=None, ...)` connects over Streamable
HTTP through FastMCP. `auth` accepts a bearer-token string, `"oauth"` or an
`httpx.Auth` instance. `MCP.stdio(name, *, command, args=(), env=None, cwd=None, ...)`
connects to an installed stdio MCP server, managing its subprocess for the client.
Both accept `include_tools=None`, `readonly_tools=()`,
`startup_timeout=10.0`, `call_timeout=60.0`, `parallel=False` and `enabled=True`.
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

Plugin authors import `SessionContext`, `SystemPromptStore`
and `ToolRegistry` from `harnessed.services`. `SessionContext` is the in-loop
session view carried by runtime hook events as `event.session`; it reads state,
appends messages, records observations, emits events and requests approvals, but cannot run, mount or close.
It is the only session service in a plugin scope; the application `Session` is
not injectable, and applications obtain the same view from `session.plugin_context`. `SessionTools` is also exported there for annotations.
These explicit service exports keep plugin code off deep internal import paths;
the root SDK remains lazy and does not re-export the service catalog.

## Approval policy

`Approvals(default=PolicyDecision.ALLOW)` and `Approvals(default=PolicyDecision.ASK)`
select the fallback decision; both accept `approver=` and `timeout=`. Tool flags
still take precedence over that fallback. `Approvals(allow=..., ask=..., deny=...)`
accepts tool references or names. Tools declare `@tool(confirm=True)` or
`@tool(readonly=True)`; these flags are mutually exclusive and are captured in
`ToolInfo` when scheduling. Explicit application rules precede tool flags and
`policy=`. `ToolApprovalGuard` provides an independent gate that survives policy
slot replacement. See [approval composition](../guides/approvals.md).

`Approvals(timeout=300)` owns the default response timeout for every approval
request in the session, including guards, `ToolContext.approve()` and direct
`session.approvals.request()` calls. A request's `timeout=` replaces this default
with a shorter or longer finite positive number of seconds; omission and `None`
inherit it. Approval waits do not consume the asking task's tool execution budget.
Missing responders and expired questions raise `ApprovalUnavailable`; in a tool
call this becomes an unavailable result. `AgentLoop` has no approval timeout.

## Tool presentation

`@tool(presentation=ToolPresentation(...))` attaches
immutable display and JSON integration metadata to native tools. Import
`ToolAnnotations`, `ToolIcon` and `ToolPresentation` from `harnessed`. Policy,
execution and review hooks expose the captured `ToolInfo.presentation`; display
values do not change model argument schemas or grant approval. See
[tool display and integration metadata](../guides/tools.md#tool-display-and-integration-metadata).

## Subagents

`Subagents(agents, *, max_parallel=3, max_calls=16, max_depth=1, timeout=180.0,
max_steps=8, token_budget=None)` from `harnessed.plugins` adds `spawn(agent, task)`,
`status(child_id, timeout=10.0)`, `send(child_id, message)`,
`wait(child_id, timeout=30.0)`, `cancel(child_id)` and `delegate(agent, task)`.
These tools are absent without the plugin. Values in `agents` implement
`ChildSessionFactory`, including `Agent`. `spawn` returns after admission;
`delegate` waits for a receipt. `status` asks the child for an explicit report,
coalesces pending requests and forwards late answers through the parent inbox.
The default depth is one; children cannot raise an inherited limit.
See [subagents](../guides/subagents.md) for approvals, limits,
recovery and ownership.

## Sandbox integrations

Import `DockerSandbox` from `harnessed.integrations.docker`, or
`BubblewrapSandbox` from `harnessed.integrations.bubblewrap`. Both own shared
shell/filesystem backends; compose them with `ShellTools()` and `FilesystemTools()`.
Docker requires an explicit `image`; Bubblewrap requires a dedicated `rootfs`.
Both accept `workspace=Workspace.copy_from(path)` or `Workspace.mount(path,
readonly=True)`, `network=NetworkAccess.NONE`, explicit `env`, `SandboxLimits`,
`max_output_bytes` and `startup_timeout`. No extra Python dependencies are required;
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
`ShellTools` accepts only `cwd`, `timeout` and `max_timeout`; configure `env` and
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
`Shell(cwd)` or `Service(ShellBackend, LocalShell(...))` in `Agent(plugins=...)`.
`BackgroundShell(cwd=".", *, timeout=600.0, max_timeout=600.0,
max_background_commands=32)` requires an existing shell backend and a live session.
It adds `start_command(argv, timeout=None)`, `read_command(command_id, wait=0.0)`,
`write_command(command_id, text="", eof=False)`, `terminate_command(command_id)` and
`list_commands()`. `Shell` and `ShellTools` retain their foreground-only tool set.

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

`Agent(*, model=None, instructions=None, prompt=None, plugins=(),
plugin_validation=PluginValidation.ERROR, store=None)` resolves reusable configuration.

| Member | Behavior |
| --- | --- |
| model | Harnessed Model or provider:model string; wrap native models with PydanticAIModel |
| instructions | Caller-owned text prepended to the selected prompt |
| prompt | Complete content document, or SystemPrompt.from_plugins(...) to keep collecting plugin blocks; defaults to collection |
| plugins | Iterable of configured plugins or PluginPack values, resolved by local slots before mounting |
| plugin_validation | ERROR rejects unresolved plugins at session startup; WARN emits PluginDependencyWarning and retains them for later activation |
| agent.plugins | Tuple of resolved plugins |
| agent.describe() | Immutable CompositionDescription with configured order, pack origins, replacement histories and exclusions |
| agent.prompt | Inspectable PromptComposition with final order, origins and format |
| store | Optional application-owned SessionStore; None keeps sessions ephemeral |
| agent.session(*, state=None, id=None, restore_outputs=()) | Open a Session from explicit state or a stored conversation; id requires a store |
| agent.run(content, state=None, **options) | Run one turn from text or a Message and return its terminal result and state |
| agent.child_session(request) | Factory boundary for a fresh parent-owned child; application code normally uses Subagents |
| SessionState.from_json(payload) / state.to_json() | Validate or encode the canonical conversation snapshot in memory |

`SessionCommit`, `SessionChange`, `SessionCheckpoint`, `SessionStore` and
`StoredSession(checkpoint: SessionCheckpoint, revision: int)`
are exported from `harnessed.sessions`. Implement `load_session(conversation_id) -> StoredSession | None`
and `save_session(conversation_id, commit, *, expected_revision) -> int`.
`SessionCommit.checkpoint` is recovery state; `SessionCommit.changes` contains
ordered `SessionChange(sequence, event)` values since the previous acknowledged
save. Map domain changes and checkpoint in one application-owned transaction.
Old changes are not emitted again on restore. Token deltas are excluded. The JSON
store ignores changes and retains its format-3 checkpoint envelope.
Checkpoints are frozen Pydantic dataclasses with typed state, execution events,
controls and resources. Adapters map them to application-owned repository models;
the SDK defines no database schema. `to_json()` / `from_json()` provide optional
canonical encoding without requiring a plugin catalog. `CheckpointExtension`
preserves application-defined event data for validation by the owning session.
Writes atomically compare revisions and return the next integer; None expects
absence. Conflicts raise `SessionWriteConflict` from `harnessed.errors`.
The application owns pool lifetime and coordinates active workers per ID.

`harnessed.integrations.json.JsonSessionStore(*, root)` ships without an extra.
It uses atomic JSON replacement and OS locks for revision checks on a local
filesystem. Persistence remains opt-in. An owned writer coalesces intermediate
state in the background and awaits commits at input, model/tool intent, result,
pause and turn-end barriers. `session.flush()` explicitly commits pending state;
closing drains the writer. Background failures reach run/result/stream consumers.
Closing a persistent paused session preserves
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

## PluginPack composition

`from harnessed import PluginPack, CompositionDescription` exposes static library
composition. `PluginPack(name: str, plugins: Iterable[PluginInput])` captures its inputs
once as a tuple; `PluginInput` is a configured kernel plugin or another PluginPack.
The name must be a nonempty string. A pack has no lifecycle of its own.

`pack.without(*types)` returns a new pack excluding all matching recipe types and
subclasses recursively. Each requested type must match a remaining recipe
(`ValueError` otherwise); non-type arguments raise `TypeError`. The original pack
and child packs remain unchanged. It does not remove dependent plugins or agent
fallbacks. `None`, booleans and arbitrary nested lists are not plugin inputs.

At each boundary, direct plugins override child-pack exports by slot. Duplicate
direct slots and conflicting sibling-pack exports raise `PluginConflict`; a
direct entry at that boundary resolves a sibling conflict. Internal child-pack
conflicts must be resolved inside that child. Nesting depth does not set global
precedence. All resolved supplied plugins override the agent's built-in
fallbacks. Unslotted recipes are additive, regardless of class or service keys.
Replacements keep the first inherited slot position.

`agent.describe()` returns a `CompositionDescription` with `plugins` and `removed`
tuples and a readable `str()` rendering. Plugin descriptions contain `name`,
`pack_path`, `origin`, `slot`, `needs`, `observes`, `provides` and recursive `replaces` tuples.
Exclusions contain `plugin` and `removed_by`. Direct inputs use an empty pack
path; built-ins use `("harnessed",)`. The result records static declarations,
not live activation or services published dynamically by setup. Only selected
recipes contribute prompts and event types. Plugin packs are not kernel plugin recipes
and cannot be mounted with `session.use()`.
See [library defaults](../guides/plugins.md#publish-library-defaults-with-pluginpack).

Start with `Agent(model="openai-responses:gpt-6.1-terra")` to select a model.
Provider strings automatically select the PydanticAI adapter. Use an explicit
`PydanticAIModel` for generation defaults or adapter options; wrap a native
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
also the default when `Agent(prompt=)` is omitted. Fixed parts appear
before complete plugin blocks in resolved plugin-list order. Nested packs expand
at their list position; removals disappear and replacements keep inherited slot
positions. Only final winners' providers run, once at agent construction.
A plugin provider returns a content document and cannot request collection itself.
A prompt built with the `SystemPrompt` constructor omits plugin blocks without
calling their providers; `str(agent.prompt)` lists the omitted plugins.
Collection strategies cannot be nested inside content.

`Agent(instructions="...")` prepends caller text. `Agent(prompt=...)` selects the
outer document or strategy. `PluginPack(name, plugins, prompt=...)` supplies a default
using normal slot precedence: parent defaults override children and explicit
agent prompts override packs. Conflicting sibling prompt defaults require a direct
choice. Caller instructions do not replace a pack's strategy. Optional `None`
entries in public plugin lists are ignored.

`Instructions(text="", title=None, description="")` is a content value, not a
plugin descriptor. `from_file(path=..., title=..., missing_ok=False)` and
`from_factory(factory=..., title=..., description=...)` defer reads or async
callbacks taking `PluginScope` until each session opens. Factory registrations
and cleanup have session ownership; ordinary plugin lifecycle registrations have
activation ownership. Passing an existing kernel `Scope` to `render()` supplies
its stable `scope.plugin_scope` view to the factory.
`print(agent.prompt)` shows final order and origins;
`await session.prompt.render()` returns the actual session text.

`async with session.use(plugin, section_delivery=SectionDelivery.ERROR)` rejects overridden
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
| use(plugin, section_delivery=SectionDelivery.ERROR) | Async context manager owning a temporary capability; CONTEXT owns guidance per activation, IGNORE skips sections |
| mount(plugin, section_delivery=SectionDelivery.ERROR) | Await a manually managed PluginHandle; call handle.aclose() to remove it |
| compact() | Summarize completed history while idle or paused; return its record or None |
| snapshot() | Export immutable conversation state at an idle boundary |
| flush() | Await pending checkpoint persistence; no I/O without a store |
| subscribe(transient=False) | Independent raw log subscription |
| events / turns | Current-turn execution buffers, released after terminal delivery |
| messages / usage / last(Event) | Retained working context, live-session usage and recent diagnostics |
| status | SessionStatus: IDLE, RUNNING, PAUSED, STOPPING, CLOSED |
| emit(event) | Admit a declared execution event or publish a transient notification |
| approvals.request(call, reason) / answer(id, decision) / revoke(id) | Ask, answer, or revoke live-session grants; `pending` / `grants` / `grant_for()` read them |
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
until compaction. `Compaction(keep_steps=5, ...)` retains five completed steps in
the active turn, including their full observations, plus the current step.

Await methods that perform durable writes. Closed handles raise SessionClosed.
SDK failures derive from `HarnessedError`, importable from `harnessed.errors`: operations
invalid in the current status raise `SessionStateError` (SessionClosed is one),
corrupt stored records raise `SessionFormatError`, and `on_max_tokens=FAIL` raises
`OutputTokenLimit`.
See [session boundaries and restart behavior](../guides/sessions.md).

Configure loop options on `AgentLoop(...)` in `plugins=`: step limits,
parallel tool limits, tool execution timeouts and context-window overrides belong to the agent
configuration. `session(id)` opens that configured runtime; it has no per-session
loop overrides. A single run may narrow the step limit with `max_steps=` and cap
the turn's billed input plus output tokens with `token_budget=`; both are stored
with the turn and survive a restart. `ToolOutputLimit(...)` and `Approvals(...)` replace their default
policy slots explicitly.

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
| @tool(metadata=(value, ...)) | Immutable application annotations keyed by exact type; no automatic model or persistence effect |
| @tool(chain=ToolChain.STOP_AFTER) | Run this tool, then skip remaining calls from the same model response; default CONTINUE |
| ToolResult(..., break_chain=True) | Request the same boundary dynamically; already-started parallel calls finish |
| Plugin[MyConfig] / config=MyConfig(...) | One frozen module-level model supplies runtime validation and the type of self.config |
| PluginState(factory) | Typed state freshly created before each mount's setup; available through cleanup |
| name = needs(T) | Stable typed service binding and lifetime requirement; no duplicate needs entry |
| name = observes(T) | Nonblocking service-or-None binding; provider changes restart the activation |
| class Capability(Plugin, provides=True) | Publish the mount under the declaring class; provides=Key selects another key |
| Plugin.create_instance() | Customize fresh instance construction; defaults to replaying explicit constructor arguments |
| Plugin.setup(scope: PluginScope) / teardown(scope: PluginScope) | Async activation lifecycle; dependencies bind first and cleanup remains framework-owned |
| PluginScope | Concrete resource/registration view; enter, effect, provide, on, dispatch, register, lookup and owner_name |
| @plugin | Kernel services and hooks in an async generator |
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
Higher-level `Plugin.setup()` and `teardown()` require service fields instead of
`Inject` parameters; standalone kernel lifecycle injection remains available.
`session.tools.explain(name)` returns a frozen `ToolExplanation` distinguishing
available, missing-service, disabled and not-registered states through
`ToolAvailability`. Both types are exported by `harnessed.services`; the existing
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
`ToolChain`, `ToolInfo` and `ToolMetadata` are exported by `harnessed`.
Skipped calls have `ToolStatus.SKIPPED` and retain their model-history pairing.
`PreStep.max_steps` is the effective turn limit; `remaining_steps` includes the
current step, `is_last_step` indicates one remaining step, and `is_final_step`
identifies an action-free reserved summary.

`PreStep` runs in the harness before the model call. Its `StepDecision` accepts
or filters queued input messages; `StepDecision.reject()` ends the turn without
calling the model. This is a plugin decision, not an LLM response. Supply automatic
environment snapshots through `session.observe()` so compaction can treat them
as step history rather than protected user instructions.

## Event classes and data

`harnessed.events` exposes `TextDeltaEvent`, `ReasoningDeltaEvent`, `ToolCallRequestedEvent`,
`ToolCallGenerationStartedEvent`, `ToolArgumentsDeltaEvent`, `ToolCallFinishedEvent`, `ApprovalNeededEvent`,
`TurnResultEvent`, `CompactionAppliedEvent` and `SpillCreatedEvent`, among other
classes. Import these full names directly; they are the canonical class names,
also used by `@hook`. `TurnEndedEvent` is the durable turn-end record;
`TurnResultEvent[T]` carries the assembled result at the end of a UI stream.
Every event has an explicit namespaced `type` discriminator.
See [events and schemas](../guides/events.md).
The [event reference](events.md) lists every built-in event, its discriminator
and whether it is part of the UI stream or only the durable audit log.

`StreamEvent[T]` describes the built-in UI union. `StreamItem[T]` includes the
application extension bases. Declare frozen dataclasses extending `DurableEvent`
or `TransientEvent` and list them on `Plugin.events`. EventRegistry supplies
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

`SessionState` is exported from `harnessed`. `state=None` starts a conversation
without history. `SessionState.from_json(str_or_bytes)` validates format version
2 and resource integrity; `state.to_json()` returns a string without I/O. State
contains retained history and provenance, feature-owned instruction state, compaction metadata,
parent/child references and terminal child receipts, and
referenced image/text resources. It contains no runtime handles, queued turns,
Python validators, approval grants or historical request/event archive.

`RunResult.state` captures that result's terminal boundary independently of later
turns. Imported state opens idle and never re-executes recorded tool calls.
`session.snapshot()` requires an idle open conversation with an empty inbox.
Reuse the same immutable state for independent continuations. See
[the session guide](../guides/sessions.md) for compaction and application storage.
