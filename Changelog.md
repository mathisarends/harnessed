# Changelog

Harnessed 0.x is **alpha software**. APIs and persisted session formats may
change in minor releases before 1.0. See the
[release notes and compatibility policy](docs/releases.md).
Version-specific breaking changes and upgrade instructions are recorded below.

## 0.15.1 (2026-11-10)

This documentation-only patch release records the Compaction API design handoff.
Runtime APIs, dependencies and persisted formats are unchanged from 0.15.0.

### Documentation

- Add the [Compaction API proposal](docs/contributing/compaction-api.md) with
  developer-facing examples, rationale, migration planning and implementation
  acceptance criteria. Link it from the contributor roadmap and documentation
  navigation.
- Describe proposed `llm`, replaceable `prompt`, `keep_last_steps`, whole-request
  context targets and before/after event measurements as future work. These
  settings and event additions are not implemented in this release; the
  [compaction guide](docs/guides/compaction.md) remains the current API contract.

### Upgrade from 0.15.0

Existing applications require no changes. No application or persistence migration
is required. Conversation state remains format **2**, checkpoints and JSON stores
format **3**, and standalone audit envelopes version **5**.

## 0.15.0 (2026-11-10)

This release adds declarative method registrations and scoped output decisions,
and fixes skill parsing. No API or persistence migration is required from 0.14.0.
Conversation state remains format **2**, checkpoints and JSON stores format **3**,
and standalone audit envelopes version **5**.

### Added

- Export `registration(recipe)` from `harnessed.kernel` for third-party method
  declarations. It attaches a `Registration` without wrapping the callable or
  changing its signature, composes with `@tool` and `@hook` in either order, and
  copies declaration metadata when extending wrappers. Dependencies are collected
  before activation; each mount receives the newly bound method and owns resources
  through its supplied scope. See [third-party declarations](docs/guides/plugins.md#third-party-method-declarations).
- Export `PrepareOutput` and `OutputDecision` from `harnessed.hooks` for phased
  completion. Session-scoped hooks run after `PrepareRequest` and before output
  preparation and token checks. `DEFER` withholds native JSON constraints and the
  reserved `final_answer` tool; validation follows the effective request schema
  without consuming correction attempts for progress text. Compose with `next_()`
  to enforce the declared contract at completion. The turn retains its declared
  output identity for restoration; final-summary steps remain unstructured. See
  [structured output](docs/guides/structured-output.md#apply-the-contract-only-after-work-finishes).

### Fixed

- Skill names now accept lowercase Unicode alphanumeric characters and single
  hyphens, trim surrounding whitespace and normalize with NFKC. Directory matching
  uses the same normalization.
- Unquoted YAML frontmatter scalars retain their literal string values, including
  numeric names, boolean-like descriptions and metadata versions. The isolated
  loader still rejects duplicate keys and unsafe tags.

### Documentation

- Shorten the README and contributor entry point, replace duplicated release
  history with links to this changelog, and update the documentation index.

### Upgrade from 0.14.0

Existing applications require no changes. Third-party decorators can replace
private declaration metadata writes with `registration(recipe)`. Output policies
default to `ENFORCE`; when opting into `DEFER`, keep continuation and completion
rules in the owning plugin. Existing persisted data needs no migration.

## 0.14.0 (2026-11-08)

This minor release introduces a restricted plugin activation scope, upgrades the
optional MCP integration, and adds public library and hook tracing. It includes
breaking API changes from 0.13.0. Persistence versions are unchanged: conversation
state is format **2**, checkpoints and JSON stores are format **3**, and standalone
audit envelopes are version **5**.

### Breaking changes

- Rename the tool execution deadline keyword `timeout` to `timeout_seconds` on
  `@tool`, `ToolRegistry.tool()`, `ToolRegistry.register()` and `ToolSpec`.
  A finite positive number overrides `AgentLoop(tool_timeout=...)`; omission or
  `None` inherits it. Shell command `timeout` and approval response `timeout`
  keep their existing names.

- The optional MCP integration now requires MCP SDK 2.3+ and FastMCP 4.1.2+.
  Replace custom `httpx.Auth` handlers with `httpx2.Auth`; custom transports must
  implement FastMCP 4's `transport_options` contract. SDK OAuth callback handlers
  return `AuthorizationCodeResult` instead of `(code, state)` tuples. The built-in
  HTTP client negotiates modern sessionless connections and falls back to legacy
  servers.
  Bind machine-to-machine OAuth credentials with `issuer=` as described in the
  [MCP guide](docs/guides/mcp.md). Multi-round input results fail without retrying
  the approved tool call.

- Tracer span names are used verbatim. Custom tracer integrations should use
  explicit names such as `harnessed.model` for runtime-equivalent spans and keep
  library names in their own namespace. Replace internal
  `span(scope, name, ...)` calls with public `harnessed.tracing.span(name,
  scope=scope, ...)`; canonical `Tracer` and `Span` identities are unchanged.
- Truncated trace content now remains valid JSON with `application/json` MIME,
  rather than a sliced `text/plain` prefix. Inspect the truncation markers and
  parse the JSON; extremely small budgets may retain only a placeholder.

- Class `Plugin.setup()` and `teardown()` are async methods accepting exactly one
  `scope: PluginScope` parameter, imported from `harnessed.kernel`. The concrete
  view provides resource and registration operations while composition and
  closure stay with the framework. Add the parameter to previously parameterless
  methods. Deferred `Instructions.from_factory` callbacks also receive this view;
  their existing session/activation ownership is preserved. Standalone kernel
  `PluginBase`, function plugins and application `session.scope` retain `Scope`.
  See the [activation contract](docs/guides/plugins.md#activation-scope-and-lifecycle-contract)
  and [design rationale](docs/contributing/plugin-scope.md).

### Added

- Tool `timeout_seconds` also accepts a synchronous resolver receiving the mounted
  plugin. It runs once per activation after setup and captures the validated
  deadline for that registration. Returning `None` inherits the loop default;
  reactivation resolves it again. External tools retain numeric deadlines.
- Add public `@traced` and `record(...)` helpers based on Browserrig's existing
  sync/async decorator pattern. Function identity and source metadata are cached
  at decoration; signatures and coroutine kind are preserved. Nested spans,
  concurrent tasks, errors and cancellation restore the active parent, while
  untraced sessions cannot record into their parent's span. Arguments and return
  values are not captured automatically. Use blocks inside generator functions.
- Capture bounded, filtered/redacted turn input and final/partial output on root
  spans, including accepted structured output. Capture portable tool definitions,
  resolved settings and output schemas in independently bounded model fields.
  Filters receive corresponding new `TraceContentKind` values; account for these
  copies when omitting sensitive content across all trace fields.
- Add provider-independent `harnessed.tracing.current()` and `span()`, explicit
  `Scope`/`PluginScope` selection, isolated ambient bindings and optional
  `OpenTelemetryTracer(trace_hooks=True)` invocation spans. Kernel instrumentation
  remains standard-library-only and preserves normal dispatch semantics.
- Preserve structured trace content when truncating and report original lengths
  and omitted item counts after filtering and redaction.

### Upgrade from 0.13.0

1. Add `scope: PluginScope` to higher-level async plugin `setup()` and `teardown()`
   methods and update deferred instruction factory annotations. Import the view
   from `harnessed.kernel`; keep full `Scope` for standalone kernel plugins and
   application composition. See the [plugin lifecycle guide](docs/guides/plugins.md#activation-scope-and-lifecycle-contract).
2. Replace tool declaration and registry `timeout=` keywords with
   `timeout_seconds=`. Keep `AgentLoop(tool_timeout=...)`, shell command timeouts
   and approval response timeouts unchanged. See [tool deadlines](docs/guides/tools.md#cancellation-and-parallel-work).
3. Upgrade the `mcp` extra together with custom authentication and transports:
   use `httpx2.Auth`, forward `transport_options`, return
   `AuthorizationCodeResult` from custom OAuth callbacks, and bind machine
   credentials to `issuer=`. See the [MCP migration guide](docs/guides/mcp.md).
4. Import library instrumentation from `harnessed.tracing`. Supply span names
   verbatim and use `span(name, scope=...)` for explicit scope selection. Update
   content filters for the new turn input/output and model definition/settings
   kinds; parse truncated content as JSON. See the [tracing guide](docs/guides/tracing.md).
5. Existing 0.13.0 snapshots, checkpoints, JSON stores and audit envelopes retain
   their format versions; no persistence migration is required for this release.

## 0.13.0 (2026-11-07)

This minor release makes plugin service bindings explicit, separates run handles
from live streams, and uses ordinary messages for conversation input and request
enrichment. It also consolidates approval timeouts and the public event API.
It contains breaking API and persistence changes from 0.12.0: conversation state
is format **2**, checkpoints and JSON stores are format **3**, and audit envelopes
are version **5**. Earlier formats are rejected without automatic migration.

### Breaking changes

- `session.stream()` now yields `RunStream[T]`, a specialized `RunHandle[T]`
  with live event iteration and `aclose()`. Plain handles from `submit()` expose
  only `id`, `done()`, `result()` and `cancel()`; remove attempts to iterate or
  close their nonexistent observation. Import `RunStream` from `harnessed` for
  streaming-specific annotations. Result, cancellation and stream context
  lifetime rules are unchanged.
- Plugin service fields now use `needs(Service)` for required activation-bound
  bindings and `observes(Service)` for nonblocking `Service | None` bindings,
  imported from `harnessed.kernel`. Replace `dependency(T)` with `needs(T)` and
  `dependency(T, optional=True)` with `observes(T)`; the old factory is removed
  without aliases. Both bind before setup, remain stable through cleanup, and
  create fresh activations and `PluginState` when their provider binding changes.
  Observing does not react to mutations inside the same service instance.
- Higher-level `Plugin.setup()` and `teardown()` reject `Inject` parameters during
  composition. Declare their services as `needs(T)` or `observes(T)` fields and
  access them through `self`; a plain `scope: Scope` remains supported for owned
  resources. Standalone kernel `PluginBase` and function plugins retain lifecycle
  injection. Tool and hook `Inject[T]` stays call-scoped and adds no plugin needs.
- Tool parameters must explicitly use `Inject[ToolContext]`. Unmarked positional
  and keyword-only `ToolContext` parameters fail registration with a migration
  hint. The marker excludes framework parameters from model schemas, including
  scripted tool references and parameter-object tools.
- Configure all approval response timeouts with `Approvals(timeout=...)`; remove
  `AgentLoop(approval_timeout=...)`. Guards, in-tool questions, SDK requests and
  forwarded approvals now inherit this same default. Per-question `timeout=`
  replaces it in either direction; omission and `None` inherit it. Approval waits
  preserve the asking task's remaining tool execution budget, and unavailable
  in-tool approvals return `ToolStatus.UNAVAILABLE`.

- Model input now uses ordinary messages throughout. Remove `context=` from
  `Agent.run()` and session `run()`, `submit()` and `stream()`; put task background
  in the input string or add request-only messages in a `PrepareRequest` hook.
  `ModelRequest.append_message(message)` returns an extended immutable request.
  `ConfigureRequest` is folded into `PrepareRequest`.
- Replace `await session.add_context(text)` with
  `await session.append_message(UserMessage(text))`; import `UserMessage` from
  `harnessed.models`. This appends durable conversation input without starting
  work. The restricted plugin `SessionContext` exposes the same operation.
- Replace `observe(Observation(source, content, refs=refs))` with
  `observe(UserMessage(content), source=source, refs=refs)`. The `Observation`
  container is removed. Pending coalescing, full text/image retention and
  inclusion receipts remain; supplied message IDs are preserved.
- Remove `ContextValue`, the public `DynamicContext` service and
  `Plugin.context_description`. Plugins contribute current facts through their
  own `PrepareRequest` hooks. Skills and Rules retain feature-owned instruction
  state independently of conversation text; runtime mount guidance is scoped to
  its request hook. No compatibility aliases are provided.
- Conversation state is format **2**, execution checkpoints and JSON-store
  envelopes are format **3**, and audit-event envelopes are version **5**.
  `message.appended` replaces `context.message`; `plugin.instructions.changed`
  records feature state without producing context XML. Start new prototype
  snapshots, stores and audit logs; old formats are rejected without migration.
  Rename `ContextMessageEvent` to `MessageAppendedEvent`, `ContextLifetime` to
  `MessageLifetime`, and `ContextValueChangedEvent` to `PluginInstructionsChangedEvent`.

- Plugins can no longer resolve the application `Session` from their scope.
  `Inject[Session]`, `needs(Session)` and `needs=(Session,)` now report a
  missing service. Use `event.session` in hooks, or declare `SessionContext` from
  `harnessed.services` for setup, tools and teardown. Replace `session.steer(...)`
  with `context.inbox.steer(...)`. `SessionContext` stays available while
  dependent plugins tear down. Applications reach the same view through
  `session.plugin_context`. `Subagents` now depends on `SessionContext` and the
  session control instead of the full session.
- Agents collect plugin instruction blocks by default, after `instructions=` and
  in resolved plugin-list order. Remove `prompt=SystemPrompt.from_plugins()` where
  it only enabled collection; keep `from_plugins(*parts, format=...)` for fixed
  content or a format. A prompt built with the `SystemPrompt` constructor now
  omits plugin blocks instead of raising `ConfigError`; `print(agent.prompt)`
  lists the omitted plugins. `PluginOrder` and the `order=` argument are removed,
  and `SystemPrompt.plugin_order` becomes the boolean `collects_plugins`.
- Open turn streams with `async with session.stream(...) as run`. The context
  yields `RunStream[T]`: iterate it for typed events, await `run.result()` for
  the same terminal result independently of observation, or `await run.cancel()`
  to cancel only that queued or active run. Context exit closes observation and
  its approval responder without aborting execution. Other queued inputs survive
  targeted cancellation; cancellation waiters do not own cleanup.
- Use `async with session.use(plugin, ...)` for a temporary capability, or
  `handle = await session.mount(plugin, ...)` for a manually managed lifetime.
- Configure display and JSON integration metadata with
  `@tool(presentation=ToolPresentation(...))`, including `title`, `tags`, `icons`,
  `annotations` and `meta`. Native registry registration uses the same argument.
  Typed Python application `metadata=` stays separate.
- `Agent.run(content, ...)` now accepts `str | UserMessage` consistently with session
  operations. Replace an explicit `prompt=` keyword with `content=`.
- `result.require_output()` returns the original validated value only for a
  completed turn; incomplete turns raise `OutputUnavailable` with their stop
  reason. Existing `result.output` remains available for validated-value inspection.

- Runtime hook events carry `event.session: SessionContext` instead of the
  application `Session`. The context keeps `id`, `status`, `log`, `inbox`,
  `approvals`, `tools`, `observe()`, `append_message()` and `emit()`; `run()`,
  `use()`, `aclose()` and `scope` are no longer reachable from a hook. Resolve
  services with `Inject[T]`, or declare `SessionContext` from `harnessed.services`
  as a plugin dependency. Compare sessions in hooks by `event.session.log`.
- Import `Scope` and `ScopeClosed` from `harnessed.kernel` in place of `Context`
  and `ContextClosed`. The root SDK no longer exports the container type.
  Replace `session.ctx` with `session.scope` and use `scope: Scope` for kernel
  parameters, including `scope=` keywords on service and runtime APIs. The old
  kernel module and names have no aliases.
- Configure `Approvals` with `allow=` and `ask=` instead of `trust=` and
  `require=`. Precedence is `deny`, `ask`, `allow`, `confirm`, `readonly`,
  `policy`, then `default`; explicit application rules now precede the callable
  policy. Select fallback defaults with `Approvals(default=PolicyDecision.ALLOW)`
  or `Approvals(default=PolicyDecision.ASK)`; the misleading `allow_all()` and
  `ask_all()` factories have been removed. Tool flags still precede the fallback.
  `@tool(confirm=True)` requests approval unless application selectors override
  it; combining it with `readonly=True` raises `TypeError`.
- Replace `ApprovalGate` with `ToolApprovalGuard` from `harnessed.plugins`.
  It retains independent gating for local or external tools across approvals-slot
  replacement. Its options and scoped grants retain their existing semantics.
- Rename the open stream return annotation `EventStream[T]` to `StreamItem[T]`.
  Import it from `harnessed.events`. `StreamEvent[T]` remains the closed built-in
  stream union. Streams now also include `ToolExecutionStartedEvent` before tool
  progress, so exhaustive consumers must handle that variant.
- `InboxTarget.NEXT_TURN` and `NEXT_STEP` now serialize as `"next_turn"` and
  `"next_step"`. Update literal routing values and stored inbox payloads;
  hyphenated values are no longer accepted.
- Built-in durable and transient events now share the public `DurableEvent` and
  `TransientEvent` bases with custom events. `SessionEvent` is their common root;
  use `DurableEvent` for persistence checks and custom persisted classes. Replace
  `BuiltinEvent` annotations with `LogEvent | StreamEvent[T]` where needed.
- Import `RuleTriggeredEvent` and `RuleAction` from `harnessed.plugins.rules`.
  Rules events are plugin-declared; readers of `rule.triggered` records must
  register `Rules.events`. Missing declarations fail strictly. Core no longer
  exports `EventKind.RULE_TRIGGERED`; use the event's plugin-owned discriminator.
- Update event tag consumers: `turn.start` → `turn.started`, `turn.end` →
  `turn.ended`, `step.start` → `step.started`, `step.end` → `step.ended`,
  `model.usage` → `model.usage.recorded`, `model.cache` → `model.cache.metrics`,
  and `context.changed` → `plugin.instructions.changed`. Matching `EventKind` members
  now use `TURN_STARTED`, `TURN_ENDED`, `STEP_STARTED`, `STEP_ENDED`,
  `MODEL_CACHE_METRICS`, `SESSION_CONTROL_CHANGED` and `SESSION_STATUS_CHANGED`.
- Replace `CacheMetricsEvent` with `ModelCacheMetricsEvent`,
  `AgentControlChangedEvent` with `SessionControlChangedEvent`, and the terminal
  stream `TurnFinishedEvent[T]` with `TurnResultEvent[T]` (`turn.result`,
  `EventKind.TURN_RESULT`). `TurnEndedEvent` remains the durable outcome record.
  Import the new classes from `harnessed.events`; there are no aliases.
- Rename `Kit` to `PluginPack`, `KitPromptPolicy` to `PluginPackPromptPolicy`
  and `ResolvedKit` to `ResolvedPluginPack`. Update imports from `harnessed`
  and references to these types; the old names have no compatibility aliases.
- Rename `PluginDescription.kit_path` to `PluginDescription.pack_path`, including
  descriptions nested in replacement histories and exclusions. Update attribute
  access and keyword construction accordingly.
- The pack rename preserves composition behavior: ordered plugin defaults,
  nesting, slot overrides, `without()` exclusions and optional prompt strategies.

### Added

- Inspect model-visible tools through the read-only `session.tools` mapping and
  its `.names` set. `harnessed.services` explicitly exports injectable prompt and
  tool services, plus the `SessionTools` view type.
- `session.tools.explain(name)` inspects the current registration even when
  unavailable to the model. `ToolExplanation` and `ToolAvailability` from
  `harnessed.services` distinguish missing services, disabled tools, available
  tools and absent registrations. Tools of waiting plugins are not registered.

### Fixed

- Reject additive user messages whose ID is still queued in the inbox, before
  retaining assets or admitting an event. Previously, appending the same message
  as a queued run silently consumed its input and left its handle unresolved,
  including after cancellation. The rejected append leaves history, the queue
  and the stored revision unchanged; use a fresh `UserMessage` for additive input.
- Correct the README and custom-event guide to use `Inject[ToolContext]`.
- Keep historical upgrade instructions in this changelog and remove the
  version-specific migration paragraph from the README.

### Upgrade from 0.12.0

1. Start new conversation snapshots, stores and audit logs. State format **1**,
   checkpoint/store format **2** and audit format **3** from 0.12.0 are rejected.
   The current versions are **2**, **3** and **5**, respectively. Back up older
   application data; there is no automatic migration or tool replay.
2. Replace `session.add_context(text)` with
   `session.append_message(UserMessage(text))`. Remove `context=` from run, submit
   and stream calls. Use input text for task background and `PrepareRequest`
   hooks returning `request.append_message(...)` for request-only facts.
   Replace `observe(Observation(...))` with `observe(UserMessage(...), source=...)`.
   Move `ContextValue`/`DynamicContext` usage to feature-owned request hooks;
   `ConfigureRequest` is now part of `PrepareRequest`.
3. Rename kernel `Context`/`ContextClosed` to `Scope`/`ScopeClosed`, and
   `session.ctx` to `session.scope`. Replace `dependency(T)` with `needs(T)` and
   optional dependencies with `observes(T)`. Move lifecycle `Inject` parameters
   to these fields; explicitly mark tool context parameters `Inject[ToolContext]`.
   Plugins and runtime hooks receive `SessionContext` from `harnessed.services`
   instead of the application `Session`; steer through `context.inbox.steer(...)`.
4. Open streams with `async with session.stream(...) as run` and annotate them
   `RunStream[T]`. Handles from `submit()` are `RunHandle[T]` without event
   iteration or `aclose()`. Context exit closes observation; `cancel()` targets
   execution. Use `session.use(...)` for scoped mounts and `await session.mount(...)`
   for manually managed handles.
5. Remove `prompt=SystemPrompt.from_plugins()` where it only enabled plugin
   instruction collection, which is now the default. Preserve explicit strategies
   that add text or set a format. Rename `Kit` and its related types to
   `PluginPack`, `PluginPackPromptPolicy` and `ResolvedPluginPack`; use `pack_path`
   in composition descriptions. See the example below.
6. Replace approval `trust=`/`require=` with `allow=`/`ask=`, the removed all-tools
   factories with explicit `default=PolicyDecision.ALLOW`/`ASK`, and `ApprovalGate`
   with `ToolApprovalGuard`. Put response timeouts on `Approvals(timeout=...)`
   instead of `AgentLoop(approval_timeout=...)`. Per-question positive timeouts
   replace the default; omission and `None` inherit it.
7. Update event names and tags using the breaking-change list above, including
   `TurnResultEvent`, `StreamItem`, `MessageAppendedEvent` and
   `PluginInstructionsChangedEvent`. Import plugin-owned rule events from
   `harnessed.plugins.rules` and supply `Rules.events` to audit readers. Use
   `ToolPresentation` for display/JSON integration metadata, `content=` for the
   `Agent.run()` keyword, and `require_output()` only for completed turns.

```python
from harnessed import PluginPack, PluginPackPromptPolicy, ResolvedPluginPack
from harnessed.plugins import Filesystem

workspace = PluginPack("workspace", [Filesystem(".")])
resolved: ResolvedPluginPack = workspace.resolve(prompt=PluginPackPromptPolicy.OMIT)
for plugin in resolved.description.plugins:
    print(plugin.pack_path)
```

Run the renamed offline example with
`uv run python -m examples.36_plugin_packs`.
See [plugin packs](docs/guides/plugins.md#publish-library-defaults-with-pluginpack).

## 0.12.0 (2026-11-04)

This minor release adds parent-owned subagent sessions and replaces the named
session context service with additive conversation input. It includes a breaking
API change from 0.11.0. Conversation snapshots remain state format **1**,
execution checkpoints and JSON-store envelopes remain format **2**, and standalone
audit envelopes remain version **3**. No storage migration is required.

### Breaking changes

- Replace the public `session.context` service with
  `await session.add_context(content: str)`. It appends a user-role text
  contribution for subsequent model requests without starting a turn, waking an
  idle agent or invoking the model. Await the write before starting dependent
  work; configured stores commit it before the method returns.
- Migrate `await session.context.set("constraints", "Include source URLs.")` to
  `await session.add_context("Include source URLs.")`. Contributions are additive:
  repeated text creates separate messages, with no names, replacement, removal or
  deduplication. They persist in conversation history across turns and restoration,
  and follow ordinary compaction rather than preserving an exact latest value.
  Use `Agent(instructions=...)` for fixed rules and `ContextValue` plugins for
  changing named facts. Inspect retained input through `session.messages` or
  `session.snapshot().history`; plugin-owned named facts remain in snapshot
  `context`. The old session API has no compatibility alias. Storage formats
  remain unchanged.

### Subagents

- Add optional `spawn`, `status`, `send`, `wait` and `cancel` tools. Spawned tasks
  can outlive a coordinator turn within the parent session and notify its inbox
  on completion. Status requests ask the child explicitly, share pending requests
  and forward late answers without completing its task or opening another driver.
- Retain bounded runtime snapshots and guidance events through persistence and
  idle parent boundaries. Drain background work on parent abort, failure, close
  and plugin removal. Keep `max_depth=1` as the configurable default; children
  cannot raise an ancestor's limit.
- Add `Subagents` with parallel delegation to isolated child sessions, explicit
  task context, depth/call/turn limits and parent-owned cancellation and cleanup.
- Add durable parent/child links, lifecycle events, routed approvals and progress,
  `session.children`, `session.parent` and recorded `session.tree_usage` inspection.
  Interrupted delegations are marked without automatically replaying child tools.
- Add a core child-session factory protocol, an offline example and a guide.

### Documentation

- Lead model configuration with `provider:model` strings. Explain when to use
  `PydanticAIModel` for generation defaults or wrap a native provider model.
- Document additive context, subagent ownership, approvals, background controls,
  persistence and recovery, with executable offline examples.

### Upgrade from 0.11.0

1. Replace `session.context.set(name, value)` with
   `await session.add_context(value)` when the value is additive conversation
   input. Await it before starting dependent work. Use `ContextValue` plugins
   for changing named facts and `Agent(instructions=...)` for fixed rules;
   `session.context.remove(name)` has no additive equivalent.
2. Keep existing snapshots and stores. The storage format versions are unchanged;
   context input lives in retained message history rather than the snapshot's
   plugin-owned named `context` mapping. Ordinary compaction applies.
3. Configure `Subagents` explicitly to enable delegation. Give each specialist
   the required task context and its own plugins and tool policies. Use an
   explicit parent session scope for background tasks; closing that scope
   cancels and drains its children. A restored parent marks unfinished children
   interrupted instead of replaying their tools.

See [context](docs/guides/context.md#add-context-without-running-the-agent),
[subagents](docs/guides/subagents.md) and the
[subagent guide](docs/guides/subagents.md).
Run `uv run python -m examples.41_subagents` and
`uv run python -m examples.42_background_subagents` for offline delegation examples.

## 0.11.0 (2026-11-02)

This minor release adds opt-in session persistence, atomic application-owned
projections, OpenTelemetry content filtering and explicit kit resolution.
The ephemeral default and explicit `SessionState` continuation remain available.
Conversation snapshots remain state format **1** and standalone audit envelopes
remain version **3**. New execution checkpoints and JSON-store envelopes use
format **2**.

### Added

- Add immutable `SessionCommit` and `SessionChange` values. Stores receive the
  checkpoint with all new, ordered durable events since their last acknowledged
  save, enabling atomic application-owned message/projection writes. Retain
  changes through background batching, compaction and turn closure; reopening
  does not re-emit historical changes. Add an executable SQLite transaction
  example with no SDK dependency in the repository layer.

- Expose a frozen, Pydantic-validated `SessionCheckpoint` from `harnessed.sessions`.
  Stores receive typed state, built-in events, controls and resources instead of
  an opaque string. Application adapters own the mapping to their existing
  repository models. Custom events round-trip as immutable `CheckpointExtension`
  values without requiring a store-side plugin catalog.

- Add opt-in `Agent(store=...)` and `agent.session(id=..., restore_outputs=...)`.
  Import `SessionStore` and `StoredSession` from `harnessed.sessions` to keep the
  root SDK small. Applications implement the structural contract directly in their
  conversation repository, with `StoredSession` values and atomic revision checks.
- Ship `harnessed.integrations.json.JsonSessionStore(root=...)` without an extra.
  Hashed IDs, atomic replacement and OS locks protect local checkpoint updates.
  No session files are written without an explicitly configured store.
- Coalesce intermediate state with one owned background writer, offload JSON
  encoding and acknowledge input, model/tool intent, receipt, pause and terminal
  barriers only after commit. `session.flush()` explicitly commits pending state;
  closing drains the writer. Background failures reach live callers and streams.
- Avoid duplicating image and text resources already present in the checkpoint
  baseline. Restore deliberately paused
  turns, limits, queues, approval grants, images and spilled text. Interrupted
  active work is repaired without automatically repeating external tool actions.

- Capture OpenTelemetry conversation and tool content with
  `capture_content=ContentCapture.redacted(*secrets)`. Register strings or
  `SecretText` wrappers, including Pydantic `SecretStr`, without a settings
  dependency. Literal values are masked throughout captured messages, tool
  arguments/results and subsequent model history before size limits. Exception
  text is separately enabled. Registered values are snapshotted and omitted from
  the policy's representation.
- Add a synchronous OpenTelemetry `content_filter` for application-specific
  transformations and field omission. Filters receive detached `TraceContent`
  values. Redaction also covers their replacements. Processing failures omit
  content with a marker while preserving the agent's outcome.
- Expose `Kit.resolve()` with explicit `KitPromptPolicy` and a frozen
  `ResolvedKit` containing resolved plugins and composition provenance.

### API and storage boundaries

- Unreleased prototype changes: `save_session` accepts `commit: SessionCommit` and
  `StoredSession.checkpoint` replaces `StoredSession.payload`. JSON encoding moves
  to store implementations; typed in-memory stores need no serialization. The JSON
  store envelope and execution checkpoint both use format **2**; unreleased format
  **1** is rejected rather than migrated.
- Stored sessions use execution-checkpoint format **2**, distinct from
  terminal `SessionState` format **1** and audit-event envelope **3**. Raw state
  JSON and former JSONL archives cannot be opened as execution checkpoints; there
  are no legacy migrations or `MemorySessions` / `JsonlSessions` aliases.
- A persistent paused session now remains recoverable after closure. Its live
  handles still close; reopen by ID and call `resume()`. Ephemeral session closure
  continues to discard queued and paused work. Python stacks and plugin state are
  not restored. Pending structured work requires explicit validator registration.
- Revision checks prevent lost updates, not concurrent external side effects.
  API servers own per-conversation execution coordination and store/pool lifetime.

### Upgrade from 0.10.0

1. Existing ephemeral agents and `SessionState` continuation need no API or format
   migration. Persistence is opt-in; `agent.session(id=...)` requires `store=`.
2. To retain conversations across requests or processes, configure
   `Agent(store=JsonSessionStore(root=...))` or implement `SessionStore` from
   `harnessed.sessions`. Save `commit.checkpoint` and any domain projections of
   `commit.changes` in one transaction with an atomic revision check. Reopen by
   ID, and register `restore_outputs` before resuming structured work.
3. If testing an unreleased persistence prototype from `main`, replace string
   payloads and checkpoint-only saves with `SessionCommit`; read
   `StoredSession.checkpoint` on load. Back up prototype data and start a format
   **2** store. Format **1** execution checkpoints and old JSONL archives have no
   automatic migration. These prototype APIs were not part of 0.10.0.
4. To capture trace content with literal secret masking, use
   `ContentCapture.redacted(*secrets)` from
   `harnessed.integrations.opentelemetry`. Add `content_filter=` for
   application-specific transformations; content capture remains opt-in.
5. Use `kit.resolve(prompt=KitPromptPolicy.INCLUDE)` to inspect or flatten a kit,
   or `OMIT` to omit kit prompt strategies recursively. Explicit prompt plugins
   and selected recipes' instruction contributions remain intact.

See [sessions](docs/guides/sessions.md), [tracing](docs/guides/tracing.md) and
[kit resolution](docs/guides/plugins.md#resolve-a-pack-into-flat-recipes).
Run `uv run python -m examples.38_session_store`,
`uv run python -m examples.39_typed_session_store` and
`uv run python -m examples.40_atomic_session_store` for offline persistence examples.

## 0.10.0 (2026-11-01)

This minor release replaces instruction descriptors and placement policies with
complete plugin instruction blocks collected in explicit plugin-list order. It
includes breaking prompt and kit APIs from 0.9.0. Conversation snapshots remain
state format **1** and the standalone audit-event envelope remains version **3**.

### Breaking changes

- Replace instruction descriptors, `@instructions`, `instruction_refs()`,
  `InstructionRef` / `InstructionProvider`, `PluginInstructions`, `Unplaced`,
  `SystemPrompt(exclude=..., unplaced=...)` and kit fragments with one plugin
  contract: override synchronous `Plugin.instructions() -> SystemPrompt`.
  Return actual content; use module constants for static text. The base method
  returns an empty document. No transitional aliases are retained.
- Reserve the Python method name `instructions` for synchronous prompt content.
  Rename tool implementation methods using that name; `@tool(name=...)` can
  preserve their model-facing tool name. Async providers and tool-decorated
  instruction methods are rejected at class definition.
- Rename `Agent(system_prompt=...)` to separate `instructions="..."` caller text
  and `prompt=SystemPrompt(...)` configuration. Caller text is prepended and
  does not replace a kit's prompt strategy.
- Collect plugin instructions explicitly with
  `SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED, format=...)`.
  The keyword-only `order` argument is required. Complete instruction blocks
  follow final configured plugin-list order; nested kits expand at their list
  position, removals contribute nothing, and slot replacements retain the
  inherited position. Setup order does not affect prompt order. Selected
  providers run once per agent, after composition. Missing collection raises
  `ConfigError` before providers run; plugin providers cannot request collection.
- Replace `Kit(fragments=...)`, `kit.instructions` and `kit.fragment(...)` with
  `Kit(prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED))`.
  Kit prompts use normal slot precedence. Parent defaults override child kits;
  explicit agent prompts override kit defaults. Conflicting siblings require a
  direct choice. Order policy and completion components explicitly in the list.
- Built-in Filesystem, Shell, BackgroundShell, Spill and Skills now implement
  `instructions()`; their `usage` / `catalog` attributes are removed. To change
  or omit guidance, override the complete method. Runtime `SectionDelivery`
  remains explicit and evaluates the new method once per mount activation.

### Added

- Accept `None` content parts in `SystemPrompt` and `None` plugins in Agent and
  Kit lists. Other unsupported values remain errors. `Instructions` remains a
  plain text/file/session-factory value, with outer-document formatting and
  session-owned effects. Repeated content is retained without identity checks.

### Fixed

- Preserve the waterfall event's result type in `Context.dispatch` and
  `EventBus.dispatch`, including generic hooks. Static checking now rejects
  synchronous and asynchronous `default=` terminals with incompatible results
  instead of accepting `Any` or widening the event's result contract. This does
  not add runtime validation of callback return values.

### Development

- Upgrade the locked Smelt CLI to 0.4.0 and enforce module cycles, definition
  ordering, necessary future imports, explicit optional defaults and test
  conventions in prek and CI. Design review hints remain advisory; warnings
  fail checks without a debt baseline. See the
  [Smelt policy](docs/contributing/smelt.md).
- Add Pylint to the locked development tools, prek and CI for control flow,
  collection mutation during iteration and override contracts. Ruff retains
  lint/format ownership and ty checks consumer typing contracts. Run
  `uv run pylint harnessed examples scripts` alongside the other
  [contributor checks](docs/contributing/development.md).

### Upgrade from 0.9.0

1. Replace `Instructions` descriptors and `@instructions` methods with one
   synchronous `instructions(self) -> SystemPrompt` method per plugin. Return
   strings, plain `Instructions` values or nested content documents inside it.
   Keep the method pure and configuration-only; use `Instructions.from_factory`
   for session-owned effects and async content. The `Instructions` value remains
   public; its descriptor behavior and the decorator are removed.
2. Replace `Agent(system_prompt="...")` with `Agent(instructions="...")` for
   caller text. Pass document configuration through `prompt=SystemPrompt(...)`
   or `prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED)`.
3. Remove `PluginInstructions`, instruction references, `exclude=` and
   `unplaced=`. Select `from_plugins(order=PluginOrder.AS_LISTED)` whenever a
   selected plugin overrides `instructions()`, even if it returns an empty
   document. Override that method to change or omit the plugin's whole block.
4. Replace kit fragments with `Kit(prompt=SystemPrompt.from_plugins(...))`.
   Review the final plugin list, including slot replacements; place policy and
   completion plugins where their complete blocks should appear. Agent caller
   text precedes fixed document content, followed by collected plugin blocks.
5. Replace built-in `.usage` / `.catalog` references and `instruction_refs()`
   inspection with the method contract and `agent.prompt` inspection. Rename
   tools whose Python method is named `instructions`; preserve the model-facing
   name with `@tool(name="instructions")` if needed.
6. Review `default=` terminals passed to kernel dispatch against the event's
   declared result type. Keep runtime mount delivery explicit with
   `SectionDelivery.CONTEXT` or `SectionDelivery.IGNORE`.
7. Keep existing state-format **1** conversation snapshots and version **3**
   standalone audit envelopes; this release requires no storage migration.

The kit can own the collection strategy while the application supplies caller
text:

```python
from harnessed import Agent, Kit, Plugin, PluginOrder, PromptFormat, SystemPrompt


class Guidance(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt("Use focused changes.")


workspace = Kit(
    "workspace",
    plugins=[Guidance()],
    prompt=SystemPrompt.from_plugins(
        order=PluginOrder.AS_LISTED,
        format=PromptFormat.MARKDOWN,
    ),
)
agent = Agent(instructions="Help with the workspace.", plugins=[workspace])
```

See [prompt composition](docs/guides/context.md) and
[kit composition](docs/guides/plugins.md#publish-library-defaults-with-pluginpack) for
provider timing, ordering and slot precedence.

## 0.9.0 (2026-11-01)

This minor release adds typed plugin recipes, explicit instruction placement,
plugin kits, Pydantic tool parameter objects and multi-event hooks. It includes
breaking plugin, dependency and prompt APIs from 0.8.0. Conversation snapshots
remain state format **1** and the standalone audit-event envelope remains
version **3**.

### Added

- Add `@tool(params=MyParams)` for a Pydantic `BaseModel` whose fields form the
  top-level tool argument schema. The method receives one validated model object;
  injected services and `ToolContext` stay separate. Preserve aliases, constraints,
  validators and default factories while enforcing strict input validation and
  rejecting extra fields. `RootModel` and combining `params` with `parameter_fields`
  are rejected. Existing per-parameter schemas remain unchanged. See
  [Pydantic parameter objects](docs/guides/tools.md#pydantic-parameter-objects).
- Extend installed-wheel verification to typed configuration objects, activation
  dependency fields and mandatory instruction placement. The distribution smoke
  test uses the current API on every supported CI platform and Python version.
- Add `Plugin[MyConfig]` with an explicit `config=MyConfig(...)` object. The type
  argument declares runtime schema selection and typed `self.config` once,
  including through generic inheritance. Configurationless plugins keep plain
  `Plugin`. The recipe and its activations retain the same frozen config object.
  The complete recipe example now also declares `FILE_EVENTS`, emits a typed
  event through `ToolContext.emit` and observes it with an independent `@hook`.
- Add typed `dependency(Service)` fields from `harnessed.kernel`: each field
  declares both a stable activation binding and its lifetime requirement.
  `dependency(key, optional=True)` captures a service or `None` without blocking
  startup and restarts on appearance, disappearance or provider replacement.
  Normalized `PluginSpec.needs` / `.observes` also drive composition descriptions
  and startup diagnostics; named keys, Protocols, inheritance and overrides work.
- Add kit-owned prompt fragments: place `kit.instructions` explicitly, or define
  `Kit(..., fragments={"instructions": (recipe, child_kit), "name": (...)})` and
  select `kit.fragment("name")`. Fragments preserve library-owned ordering,
  omit removed recipes and include selected slot replacements through nesting.
  Individual instruction references retain exact recipe identity.
- Add `harnessed.testing.mount_plugin(recipe, services=(Service(Contract, fake),))`
  to yield a typed active instance with guaranteed cleanup and a tool registry,
  without an agent or model. `PluginHandle.instance` exposes the current kernel
  activation. See `uv run python -m examples.37_plugin_recipes` for the new API.
- Add `Kit(name, plugins)` and immutable `kit.without(*types)` for library-owned
  plugin defaults. Direct entries override child-kit exports by explicit slot;
  conflicting sibling kits require a local direct decision regardless of nesting.
  Class and service-key equality never imply replacement. Built-in agent plugins
  remain the final fallback, and removed recipes never mount.
- Add `agent.describe()` returning an immutable `CompositionDescription` with
  final order, kit paths, declared dependencies/services, replacement histories
  and exclusions. See the [composition guide](docs/guides/plugins.md) and offline
  example `uv run python -m examples.36_plugin_kits`.
- Support `@hook(EventA, EventB, ...)` to forward several event types to one
  sync or async handler. The handler accepts every listed event, usually through
  a union annotation, while retaining event, result and continuation type checks.
  Shared handlers support injected dependencies; `priority` and `prepend` apply
  to every listener, and plugin disposal removes all of them. Observer and
  waterfall events cannot share a decorator. See the
  [hook guide](docs/guides/tools.md#hook-signatures) for examples and the behavior
  of repeated or overlapping event keys.

### Development

- Replace the development dependency `pre-commit` with `prek`. Run
  `uv run prek run --all-files`; existing contributors replace installed Git
  hooks once with `uv run prek install --force`. Hook definitions remain in
  `.pre-commit-config.yaml`.
- Adopt the locked Smelt CLI 0.3.0 architecture and convention checks in
  prek and CI. The executable policy is `smelt.yaml`; see the
  [contributor guide](docs/contributing/smelt.md) for its invariants and scope.

### Breaking changes

- Pass prompt content directly as positional arguments:
  `SystemPrompt("Help with documents.", workspace.instructions, "Answer concisely.")`.
  Replace `SystemPrompt(sections=parts)` with `SystemPrompt(*parts)`; `format`,
  `exclude` and `unplaced` remain keyword-only options. The `sections=` constructor
  keyword is removed.
- Remove `Config = MyConfig`, the repeated `config: MyConfig` declaration and
  arbitrary configuration keyword forwarding from the Plugin base constructor.
  Use `class Files(Plugin[FilesConfig])` and
  `Files(config=FilesConfig(max_read_bytes=100_000))`. Missing, wrong-model and
  mutable configurations fail before activation. Existing typed feature
  constructors retain their signatures and build explicit models internally.
- Rename prompt content `SystemSection` to `Instructions`, `@system_section` to
  `@instructions`, bound/provider types to `InstructionRef` / `InstructionProvider`,
  and `PluginSections` to `PluginInstructions`. The renderer plugin formerly
  exported as `harnessed.plugins.Instructions` is now `PromptRenderer`. No aliases
  are retained. Plugin metadata inspection is `plugin.instruction_refs()`.
- Require explicit placement or exclusion for every selected plugin instruction,
  including empty content, a string system prompt, and no application prompt.
  `SystemPrompt` defaults to `Unplaced.ERROR`; `PluginInstructions()` and explicit
  `Unplaced.APPEND` remain visible opt-ins for accepting future contributions.
  Coverage errors precede provider calls and file reads. Class docstrings remain
  developer documentation and never become model instructions.
- Replace class-level `Inject[T]` with `name = dependency(T)`. Call-scoped `Inject`
  retains its existing behavior and does not declare lifetime requirements.
  Dependency fields are read-only and available only during an activation,
  including teardown and owned cleanup. Optional field changes reset activation
  state. Extra instance `needs=` cannot remove field or registration requirements.
- Default `Plugin.create_instance()` now replays explicit constructor arguments
  instead of shallow-copying arbitrary attributes. Constructors must be repeatable
  configuration/declaration code; allocate resources in setup. Constructor-created
  containers are fresh per activation, while explicitly supplied clients retain
  identity. Custom factories define sharing explicitly. Plugin configuration
  models must be frozen, and `plugin.config` cannot be replaced.
- Direct tool calls on recipes or disposed activations raise `RuntimeError`.
  Declarative tool and prompt references remain available on recipes; prepared
  calls keep their original activation and never follow replacement registrations.
- Resolve plugin composition before collecting prompt contributions and event
  declarations. Replaced and excluded plugins no longer contribute metadata or
  execute configuration-derived prompt providers. Explicit plugin groups follow the
  final resolved order, including the inherited slot position of replacements,
  rather than the original supplied list. Explicit references to discarded
  recipes fail instead of following a replacement.

### Upgrade from 0.8.0

1. Replace Config aliases and repeated annotations with `Plugin[MyConfig]`.
   Pass `config=MyConfig(...)` to plugins inheriting the base constructor.
   Custom constructors use `super().__init__(config=MyConfig(...))`; plain
   configurationless plugins continue to call `super().__init__()`.
2. Rename instruction declarations and decorators to `Instructions` and
   `@instructions`. Use explicit `SystemPrompt(files.usage, ...)` or
   `kit.instructions`; omit contributions with `exclude=(files.usage,)`.
   Replace `SystemPrompt(sections=parts)` with `SystemPrompt(*parts)` and rename
   the built-in renderer plugin to `PromptRenderer`.
3. Replace service attributes `backend: Inject[Backend]` and duplicate `needs`
   entries with `backend = dependency(Backend)`. Use `optional=True` for stable
   optional bindings, and keep extra `needs` for requirements without an attribute.
4. Make constructors repeatable, keep models frozen, and move acquisition into
   setup with Context cleanup. Use the active instance for direct tool tests via
   `mount_plugin`. Review deliberate sharing in custom `create_instance` methods.

For the kit-composition additions:

1. Replace library-specific default merging and mirrored agent subclasses with
   `Kit` factories where useful. Declare namespaced slots on replaceable
   capabilities. Existing direct plugin lists and explicit lifetime `needs`
   retain their contracts; no service-key or class-based replacement is inferred.
2. When prompt order matters, use an explicit `SystemPrompt` referencing selected
   recipes. Remove references to discarded recipes or point them at the selected
   replacement. A `PluginInstructions()` rest slot includes remaining selected sections.
3. Resolve conflicting sibling-kit slots with a direct entry at their shared
   boundary. Resolve internal kit conflicts inside that kit. Inspect
   `agent.describe()` before opening a session to review the resulting decisions.

## 0.8.0 (2026-10-30)

This minor release replaces automatic session archives with explicit conversation
state and adds an application-owned OTLP/HTTP provider helper. It includes
breaking session, persistence and output-restoration API changes from 0.7.0.
Conversation snapshots use state format **1**; older JSONL archives are not
accepted or automatically migrated. The standalone audit-event envelope remains
version **3**.

### Explicit conversation state (breaking)

- Replace automatic agent-owned session archives with immutable `SessionState`.
  Continue with `agent.run(prompt, state=state)` or `agent.session(state=state)`;
  export through `result.state` or an idle `session.snapshot()`. Applications own
  storage, conversation keys, expiration and competing writers.
- Add a validated state format **1**, including retained context, provider replay
  data and referenced image/spill bytes. JSON import/export performs no storage
  I/O. Unknown versions and corrupt resources fail explicitly.
- Compaction releases discarded context and runtime indexes. Completed turns'
  execution buffers are released after result delivery. Earlier snapshots and
  caller-held results remain independent owners of their data.
- Remove `MemorySessions`, `JsonlSessions`, `Durability`, `SessionStore`,
  `SessionPersistence`, `SessionRepository`, `agent.sessions`, ID-based reopening,
  `restore_outputs`, fork APIs and ordered `EventLogCodec` persistence. Remove
  storage-only `SessionLocked`, `LogTailRecoveredEvent` and `InMemoryPersistence`.
- Conversation snapshots restore idle conversations at terminal turn boundaries.
  Live pause/resume, streaming, tools and local cancellation cleanup remain.
  Paused executions, pending inbox work, approvals, processes and Python plugin
  objects are not restored. Approval grants and Rules counters start fresh in a
  new live session. Custom audit events can be stored externally through hooks.

### OpenTelemetry provider lifecycle

- Add `otlp_provider()` in `harnessed.integrations.opentelemetry` as an async
  context manager around an application's agents and sessions. It creates a
  `TracerProvider`, `BatchSpanProcessor` and OTLP/HTTP exporter without replacing
  the global provider. Install `harnessed[otel]` to use the optional integration.
- Require a nonempty `service_name`; accept an explicit trace ingestion endpoint
  and resource attributes. Standard OpenTelemetry environment variables configure
  exporter endpoints, headers, timeouts, resources and sampling. Explicit values
  take precedence, with `service_name` overriding the `service.name` resource.
- Shut down the owned provider outside the event loop after application work,
  including setup failures, application errors and task cancellation. Shutdown
  attempts to export pending batches; it does not confirm backend ingestion.
  Close sessions before leaving the provider context. Closing a session or
  unmounting `OpenTelemetryTracer` does not shut down a shared provider.
- Expand the [tracing guide](docs/guides/tracing.md) with application lifespan,
  shared-provider and environment configuration examples. Add an `--otlp` mode
  to `uv run python -m examples.29_tracing`; the default remains an offline
  console example. Cover batched HTTP delivery and cleanup with local collectors.

### Upgrade from 0.7.0

1. Remove `MemorySessions`, `JsonlSessions`, durability configuration and custom
   archive backends. Replace ID-based reopening and `agent.sessions` access with
   application-owned storage and explicit `SessionState` values. The application
   owns conversation keys, authorization, revisions, expiration and deletion.
2. Continue with `agent.run(prompt, state=state)` or `agent.session(state=state)`.
   Save `result.state.to_json()` after a terminal turn; load it with
   `SessionState.from_json(payload)`. Use `session.snapshot()` only while idle,
   with no queued inputs. Omit `state` to start a new conversation.
3. Back up older JSONL archives and their `.assets` and `.files` directories before
   upgrading. They cannot be passed to `SessionState.from_json()`; there is no
   automatic archive migration. Start new state-format-1 conversations or arrange
   an application-specific conversion before upgrading. New snapshots contain
   retained image and spill bytes, without sidecar resource directories.
4. Replace fork APIs with independent runs receiving the same immutable snapshot.
   Paused executions, queued work, pending approvals and live processes cannot
   be restored. Live pause/resume and cancellation still work within one session;
   approval grants and Rules counters start fresh when a new session opens.
5. Remove `restore_outputs` and configure the desired output validator or
   `OutputContract` on each new turn. Snapshots retain conversation messages and
   provider replay state, not Python validators, agent configuration or plugins.
6. Store complete audit histories externally through subscriptions or hooks.
   Conversation snapshots retain working context rather than an execution archive;
   ordered `EventLogCodec` persistence is removed. The standalone audit-event codec
   keeps its own versioned envelope.
7. Existing OpenTelemetry providers continue to work. To adopt `otlp_provider()`,
   open it once around sharing sessions and pass its result to
   `OpenTelemetryTracer(tracer_provider=provider)`. Content capture remains an
   explicit tracer option and is disabled by default.

See the [session guide](docs/guides/sessions.md),
[structured-output guide](docs/guides/structured-output.md) and examples 14 and 17
for explicit state and application-owned storage.

## 0.7.0 (2026-10-30)

This minor release adds typed model settings, immutable multimodal observations,
session-owned image assets and opt-in background shell commands. It includes
breaking model, message and observation API changes from 0.6.0 and advances the
session record format from **2** to **3**. See the upgrade steps below.

### Model configuration and replay state

- Add immutable `GenerationSettings` on models and typed per-turn overrides.
  Move `max_tokens` out of `AgentLoop` and `ModelRequest` into settings; expose
  JSON provider-specific options through `provider_options`.
- Keep model selection on the configured `Model`. Remove `ModelRequest.model`
  and record the selected `ModelInfo` on `ModelRequestedEvent.model`. Requests,
  structured output and compaction use the captured model binding throughout.
- Require `PydanticAIModel(native_model)` when passing a native model to `Agent`.
  Custom model implementations now expose `settings: GenerationSettings`.
- Replace raw assistant provider dictionaries with `ProviderState`, including
  format/version and provider/model identity. Clear state with `None` when
  deliberately discarding signed replay data; incompatible bindings fail explicitly.

### Background shell commands

- Add the explicit `BackgroundShell` plugin with start, live output inspection,
  stdin/EOF, process-tree termination and session-inbox completion notifications.
  Existing `Shell`/`ShellTools` tools remain unchanged. See the
  [developer guide](docs/guides/background-shell.md) and offline example 35.

### Message names and multimodal observations

- Rename `System`, `User`, `Assistant` and `ToolReply` to `SystemMessage`,
  `UserMessage`, `AssistantMessage` and `ToolMessage` in `harnessed.models` and
  throughout the SDK. Remove the old class names; `user()` and `system()` remain
  convenience constructors.
- Replace `Observation.full` and `.summary` with one immutable `content` value:
  text or a tuple of text and image parts. Automatic `PreStep` capture still
  uses `session.observe()`. Recorded snapshots keep their complete content,
  identity and position until compaction or an explicit retention policy.
- Compact observations and their images together in the same request. Existing
  `Compaction(keep_steps=5, ...)` retains five completed steps in the active turn
  plus the current step. Between compactions, previous snapshots stay unchanged.
- Make `LatestObservationOnly()` an explicit one-snapshot-per-source policy and
  remove `max_summaries`. Omitting it retains snapshots until compaction.
- Remove `ObservationForm`. Each `IncludedObservation` now carries `source`,
  `refs`, `turn_id`, `step` and `message_id`. A receipt covers the complete final
  text/image message; editing text or removing an image removes its receipt.

### Session storage

- Include the model, observation and receipt changes in one canonical format,
  version **3**. Reject versions 1 and 2 without rewriting their logs; remove
  legacy request-ID synthesis. No automatic format migration is provided.
- Store inline images from observations, user inputs and tool results as typed
  `ImageReference(AssetRef(...))` values. JSONL writes the binary data once per
  session under `<id>.assets/<sha256>` before committing a referencing event.
  Historical requests retain references rather than Base64 payloads.
- Resolve only images selected for the model request, after compaction and budget
  checks. File I/O runs off the event loop; a per-session LRU reuses encoded images
  with a 16 MiB payload / 256-entry limit. Oversized images bypass the cache.
- Read local image attachments directly into the asset store. Applications can
  use `session.assets.put(bytes, media_type=...)` and `ImageReference` to avoid a
  Base64 round trip. External HTTP image URLs remain external.
- Reopen image histories without loading image files. Verify size and SHA-256 on
  reads, copy referenced images when opening forks, and remove the asset namespace
  when deleting a session. Missing fork images fail before writing the fork log.
- Keep cache management, image normalization, fork-copy helpers and concrete asset
  backends internal. Expose read-only `session.assets` / `log.assets` ownership and
  `log.resolve_request(request)` for manual replay. `AssetRef.verify(bytes)` remains
  available to custom backends; `File`/`Image` conversion helpers are private.
- Extend custom `SessionStore` implementations with `assets(session_id)` returning
  an `AssetStore`. Low-level persisted `SessionLog` construction requires an
  explicit asset store too. Memory sessions retain deduplicated binary assets in memory;
  JSONL sessions retain them on disk. Compaction does not delete audit assets.

### Session cleanup

- Share one cleanup operation across concurrent `session.aclose()` calls. Every
  successful close waits for driver shutdown and resource cleanup; cancelling one
  caller leaves cleanup running for the remaining callers. Release live resources
  once through `SessionClosing`, including background shell processes.

### Upgrade from 0.6.0

1. Import `GenerationSettings` from `harnessed.models` and configure generation
   defaults on the model. Pass typed per-turn overrides to `session.run()` or
   `submit()`. Move `max_tokens` out of `AgentLoop` and `ModelRequest`, and put
   provider-specific JSON options in `provider_options`.
2. Give custom models a `settings: GenerationSettings` attribute. Wrap native
   provider models in `PydanticAIModel` explicitly. Read selected model metadata
   from `ModelRequestedEvent.model` instead of `ModelRequest.model`. Move defaults
   from `ModelInfo` to the model. Configure `ModelSummarizer` with the desired model
   directly instead of using `model_name`. Replace the `Model` service to select
   a different model for subsequent steps; a prepared step keeps its captured binding.
3. Replace raw assistant `provider_state` dictionaries with `ProviderState`.
   Use `None`, rather than `{}`, when deliberately discarding replay state.
4. Import `SystemMessage`, `UserMessage`, `AssistantMessage` and `ToolMessage`
   from `harnessed.models` in place of their old short class names.
5. Pass `Observation(source, content, refs=())` instead of full and summary text.
   Put screenshots in the same content tuple as their text. Omit
   `LatestObservationOnly` to retain snapshots until compaction, or instantiate
   it without arguments to keep only the latest per source. Review budgets
   because old observations are no longer shortened after every step.
6. Use final inclusion refs and explicit turn/step/message identities instead
   of `ObservationForm` or message-ID parsing. Browser integrations can replace
   request-time image insertion with multimodal observations. The default
   compaction summarizer reads text and captions, not image pixels; record
   important visual findings as text if they must survive summarization.
7. Implement `SessionStore.assets(session_id)` in custom storage plugins. Consumers
   of recorded messages and tool results must accept `ImageReference` alongside
   `ImagePart`. Use `session.assets.read(ref.asset)` for bytes, or
   `await session.log.resolve_request(request)` for a provider-ready request.
   Back up each JSONL log together with its `.assets` and `.files` directories.
8. Back up version-2 sessions and start a new version-3 store. Existing published
   formats are not migrated. See the [context guide](docs/guides/context.md),
   [model guide](docs/guides/models.md) and [session guide](docs/guides/sessions.md).
9. Opt in to `BackgroundShell` alongside `Shell` or an explicitly registered
   backend. Keep the session context open while jobs are needed. Running handles
   and captured output are not restored from persisted sessions or forks.

For example, configure defaults on the model and override them per turn:

```python
from harnessed import Agent
from harnessed.models import GenerationSettings, PydanticAIModel

model = PydanticAIModel(
    "openai:gpt-4.1",
    settings=GenerationSettings(max_tokens=4096, temperature=0.2),
)
agent = Agent(model=model)

# Inside an async function:
# result = await agent.run("Give a short answer.", settings=GenerationSettings(max_tokens=512))
```

## 0.6.0 (2026-10-30)

This release adds explicit Docker and Bubblewrap sandboxes and hardens MCP
transport failures and cleanup. It includes breaking shell/filesystem backend
changes from 0.5.0; the session record format remains version **2**.

### Sandbox integrations and backend contracts

- Add explicit Docker and Bubblewrap sandbox integrations, shared shell/filesystem
  backends, session-owned cleanup, bounded copy workspaces and binary exports.
  Bubblewrap verifies cgroup-v2 limits; both fail rather than falling back to local
  execution. Native runtime dependencies remain optional.
- Expose the full filesystem tool set through `FilesystemTools` and interpret
  `ShellTools(cwd=...)` in the selected backend's filesystem. Add a typed factory
  contract for additional sandbox backends and native-runtime opt-in tests.
- Expose ready `shell` and `file_system` services through the sandbox lifecycle
  contract and `sandbox.services(session)`. Keep worker transport internal and add
  bounded binary reads to filesystem backends for explicit exports.
- Preserve sandbox startup timeout errors, include queueing in request deadlines,
  use a common UnicodeError decoding contract and reject oversized file ranges
  rather than silently shortening them. Execute sandbox grep in one worker request
  and cache Docker helper source per backend. ShellTools only configures tool
  arguments; backend environment and capture limits belong to the backend.
- Add the [sandbox guide](docs/guides/sandboxes.md), typed backend extension
  contracts and the offline `uv run python -m examples.34_sandbox` example.
  CI exercises the native Docker integration on Linux/Python 3.12; Bubblewrap
  runtime tests require an explicitly configured Linux host and rootfs.

### MCP transport reliability

- Close unusable MCP connections and hide their tools after protocol errors,
  invalid tool responses, terminated HTTP sessions and stdio disconnects.
  Malformed transport messages fail promptly; ordinary tool `isError` results
  leave the connection available. Failed operations are not automatically retried.
- Wake all pending MCP calls when cancellation, timeout or unmount closes their
  shared connection, and drain the owned request tasks during cleanup.
- Apply a five-second network inactivity timeout to HTTP session termination so
  an unresponsive DELETE endpoint cannot indefinitely block mount cleanup.
- Add real-socket HTTP coverage for stateful/stateless JSON and SSE sessions,
  OAuth discovery, PKCE, token refresh/reuse, redirects, credential forwarding,
  parallel calls, failures and cleanup. Extend stdio tests for process crashes,
  malformed output, minimal environment inheritance and literal arguments.
- Document MCP deployment trust boundaries, server isolation and resource limits
  in the [MCP guide](docs/guides/mcp.md#deployment-boundary).

### Upgrade from 0.5.0

1. Move `env` and `max_output_bytes` from `ShellTools(...)` to the supplied
   `LocalShell(...)` or sandbox integration. `ShellTools` now accepts only `cwd`,
   `timeout` and `max_timeout`. When using `Shell(implementation=...)`, configure
   environment and capture limits directly on that backend; supplying these
   options to `Shell` as well raises `ValueError`. `Shell` still accepts them
   when it owns its default local backend.
2. Interpret `ShellTools(cwd=...)` relative to the selected backend's filesystem.
   It is no longer resolved to an absolute host path during mount. Sandboxes
   require workspace-relative paths. Custom `ShellBackend` implementations must
   include queueing and launcher startup in timeouts, propagate `TimeoutError`
   on expiry and avoid automatically replaying failed commands.
3. Extend custom `FileSystemBackend` implementations with bounded
   `read_bytes(path, *, limit=None)` and
   `grep(query, *, pattern="**/*", limit=100) -> SearchResult` methods. Import
   `SearchResult` and the optional `search_files` fallback from
   `harnessed.plugins.filesystem`. Invalid UTF-8 raises `UnicodeError`; requested
   ranges that exceed a backend's byte budget must raise `ValueError` rather
   than silently truncate. Implement remote grep in the backend to batch work.
4. Review tool allowlists and prompt composition when mounting `FilesystemTools`:
   it now declares `usage` guidance and exposes discovery, editing and deletion
   as well as reading and writing. Runtime mounts must explicitly select
   `section_delivery`, as for other plugins with system sections. Ordinary
   mutating tool calls still use the existing approval policy.
5. Keep existing version **2** session stores; no migration is required from
   0.5.0. Sandbox files are not persisted with the conversation or snapshotted
   by forks. Export copied results before session close; reopening creates a
   fresh runtime. Failed MCP connections now hide their tools and reject later
   calls; create a fresh connection instead of retrying on the closed mount.

Sandbox integrations are opt-in and need no new Python extra. Install and
configure the native runtime and its image/rootfs explicitly, then compose the
sandbox with `ShellTools()` and `FilesystemTools()`. Existing `Shell` and
`Filesystem` plugins continue to own their local backends. See the
[sandbox guide](docs/guides/sandboxes.md) for resource controls, direct typed
services, exports and deployment boundaries.

## 0.5.0 (2026-10-30)

This release adds tool metadata, action-chain boundaries, reserved final-step
summaries and correlated compaction/model activity. It extends the event and hook
contracts from 0.4.0; the session record format remains version **2**.

### Tool metadata and action chains

- Add `@tool(metadata=(...))` with typed, process-local annotations. Import
  `ToolInfo`, `ToolMetadata` and `ToolChain` from `harnessed`. Policy, execution and
  result-review hooks expose the captured registration through `event.tool`,
  including for schema-defined external tools. Missing registrations have
  `tool=None`; metadata is neither sent to the model nor automatically persisted.
- Add `ToolChain.STOP_AFTER` for a sequential boundary after earlier parallel
  calls finish, and `ToolResult(..., break_chain=True)` for a dynamic boundary.
  Unstarted later calls in the response receive `ToolStatus.SKIPPED` without
  approval or execution; already-started parallel calls finish normally. Break
  signals survive result review and persistence, including crash recovery.
- See [tools and hooks](docs/guides/tools.md) for metadata lookup, external
  registration and the exact denial, failure and parallel-execution behavior.

### Step progress and final summaries

- `PreStep` exposes the effective `max_steps`, `remaining_steps` (including the
  current step), `is_last_step` and `is_final_step`. Turn-start records capture
  the resolved step limit and final-step policy for pause/reopen.
- Add opt-in `AgentLoop(final_step=FinalStepPolicy.SUMMARIZE)`, importing the
  policy from `harnessed.plugins`. The last available step becomes a text-only
  summary within the existing limit. Runtime execution blocks any returned tool
  calls, and continuation hooks cannot extend the limit. The default remains
  `FinalStepPolicy.NONE`.
- A reserved summary ends with `StopReason.MAX_STEPS` and `completed=False`.
  Structured-output runs expose the summary through `.text`; `.output` raises
  `OutputUnavailable`. Token budgets and provider failures may prevent a summary.
  See [step progress](docs/guides/sessions.md#step-progress-and-a-reserved-summary).

### Compaction, model activity and event documentation

- Add transient `CompactionStartedEvent` and `CompactionFinishedEvent`, correlated
  by `compaction_id`, with applied, discarded, failed and cancelled outcomes.
  `CompactionAppliedEvent` remains the durable history projection and records
  estimated token savings rather than provider billing.
- Add typed `ModelAttemptStartedEvent`, `ModelRetryScheduledEvent` and
  `ModelAttemptFinishedEvent`, and forward `ModelRequestedEvent` and
  `ModelRequestFailedEvent` to UI streams. Provider retries, context recovery and
  structured-output correction retain separate counters and correlation.
- Adapters can report observed provider attempts through chunks or awaited,
  task-scoped `report_model_diagnostic()` callbacks. Hidden SDK counters remain
  unknown; this telemetry adds no provider retry policy and does not change
  provider exception behavior. Runtime retry correlation is persisted on model
  requests; transient activity is not replayed.
- Document durable versus transient events, request/turn completion, cancellation
  and partial results in the [event reference](docs/reference/events.md) and
  [streaming guide](docs/guides/streaming.md). Add the offline basic-stream example
  `uv run python -m examples.33_basic_stream`.

### Upgrade from 0.4.0

1. Pass the effective keyword-only `max_steps=` when manually constructing
   `PreStep` events. Hook consumers can use `remaining_steps`, `is_last_step` and
   `is_final_step` instead of deriving progress from recipe defaults.
2. Update exhaustive stream/event handlers for compaction and model activity, and
   tool-result handlers for `ToolStatus.SKIPPED` and `break_chain`. A failed model
   request or finished attempt does not itself finish a turn; inspect
   `TurnFinishedEvent.result` for the run outcome. Keep unknown retry counts as
   `None` rather than treating them as zero.
3. Keep existing version **2** session stores; no format migration is required
   from 0.4.0. Update strict application schemas for the new defaulted fields on
   model requests, turn starts and tool results before reading 0.5.0 records.
   Older readers may reject those records. Existing paused turns without a
   resolved step limit use the reopening agent's configured limit.
4. Tool metadata, action-chain boundaries and final summaries are opt-in.
   Existing tools continue their chain by default. If enabling summaries for
   `output=T` runs, check `completed` before accessing `.output`; a reserved
   summary is an incomplete text result, not a validated `T`.

### Client integration examples

Pin `harnessed==0.5.0` together with the provider extras your application uses.
The examples below use only public application imports. Existing clients can
keep their current defaults; enable each new behavior explicitly.

#### Tool metadata, captured hooks and a context boundary

Mount `Workspace()` in `Agent(plugins=[...])`. The state belongs to the mounted
plugin. A workspace switch still requires the application's ordinary approval
decision; after execution starts, it stops unstarted calls from the same response:

```python
from dataclasses import dataclass

from harnessed import Plugin, PluginState, ToolChain, ToolResult, tool
from harnessed.hooks import Next, ReviewToolResult
from harnessed.kernel import hook


@dataclass(frozen=True)
class AuditTag:
    category: str


class Workspace(Plugin):
    active = PluginState[str](lambda: "default")

    @tool(metadata=(AuditTag("workspace"),), chain=ToolChain.STOP_AFTER)
    async def switch(self, name: str) -> str:
        self.active = name
        return f"Active workspace: {name}"

    @tool(readonly=True, metadata=(AuditTag("workspace"),))
    async def inspect(self) -> str:
        return self.active

    @hook(ReviewToolResult)
    async def review(self, event: ReviewToolResult, next_: Next[ToolResult]) -> ToolResult:
        result = await next_()
        tag = event.tool.metadata.get(AuditTag) if event.tool is not None else None
        return result.with_note(f"Audit category: {tag.category}") if tag else result
```

`metadata` allows one immutable value per exact Python type. Its values stay
process-local; write an application event if they must survive reopening. The
same `event.tool` snapshot is available in `ToolPolicy` and `ExecuteTool`; it
retains the originally scheduled registration even if a name is rebound.

`STOP_AFTER` cannot be combined with `parallel=True`. A denial or disabled tool
does not break the chain; an attempted boundary tool does, including on error or
timeout. For a dynamic boundary return `ToolResult("State changed", break_chain=True)`
or set the flag in result review. Already-started parallel calls complete normally.
Handle `ToolStatus.SKIPPED` as an explicit unexecuted result; it has no execution
receipt and requests no approval. Neither a chain break nor a skipped call ends
the whole turn: the next model step may continue within its existing limits.

#### Reserve a final summary without treating it as validated output

Pass your configured model to this function. A normal structured run exposes a
validated `Answer`; a reserved final summary is read only through `.text`:

```python
from pydantic import BaseModel

from harnessed import Agent
from harnessed.models import Model
from harnessed.plugins import AgentLoop, FinalStepPolicy


class Answer(BaseModel):
    summary: str


async def report(model: Model) -> str:
    agent = Agent(
        model=model,
        plugins=[AgentLoop(max_steps=4, final_step=FinalStepPolicy.SUMMARIZE)],
    )
    result = await agent.run("Summarize the findings.", output=Answer)
    if result.completed:
        return result.output.summary
    return result.text
```

The four-step limit includes the summary: at most three ordinary steps remain.
With `max_steps=1`, the only request is a summary. Per-run `max_steps=` overrides
also determine the reserved step. A summary has no tools or output schema;
unexpected returned tool calls are skipped by the runtime. It ends with
`StopReason.MAX_STEPS` and `completed=False`, never a validated `Answer`.
Provider exceptions still propagate, and a token budget can stop before a summary.

Hook clients import `PreStep` from `harnessed.hooks`. Read `event.max_steps` and
`event.remaining_steps` instead of recipe defaults; the remaining allowance
includes the current step. `is_last_step` can describe an ordinary final step,
while `is_final_step` identifies the reserved summary. Manually constructed
events must now provide `PreStep(session, messages, step, max_steps=effective_limit)`.

#### Extend the live stream consumer

This consumer logs activity, handles skipped calls and explicitly denies approval
requests. Replace that denial with your application's decision path when needed:

```python
from contextlib import aclosing

from harnessed import ApprovalAnswer, Session, ToolStatus, events


async def show(session: Session) -> None:
    async with aclosing(session.stream("Review the workspace.")) as stream:
        async for event in stream:
            match event:
                case events.TextDeltaEvent(text=text):
                    print(text, end="", flush=True)
                case events.CompactionStartedEvent(compaction_id=key):
                    print("Compaction started:", key)
                case events.CompactionFinishedEvent(compaction_id=key, outcome=outcome):
                    print("Compaction finished:", key, outcome)
                case events.ModelRequestedEvent(request_id=key):
                    print("Request submitted:", key)
                case events.ModelRequestFailedEvent(request_id=key, outcome=outcome):
                    print("Request closed:", key, outcome)
                case events.ModelRetryScheduledEvent() as retry:
                    print(
                        "Retry planned:",
                        retry.retry_kind,
                        retry.retry_group_id,
                        retry.attempt,
                        retry.reason,
                        retry.delay_seconds,
                    )
                case events.ModelAttemptStartedEvent() as attempt:
                    print(
                        "Attempt started:",
                        attempt.retry_kind,
                        attempt.request_id,
                        attempt.retry_group_id,
                        attempt.attempt,
                    )
                case events.ModelAttemptFinishedEvent() as attempt:
                    print(
                        "Attempt closed:",
                        attempt.retry_kind,
                        attempt.request_id,
                        attempt.outcome,
                        attempt.attempt,
                        attempt.partial,
                    )
                case events.ToolCallFinishedEvent(call=call, result=result):
                    if result.status is ToolStatus.SKIPPED:
                        print("Not executed:", call.id, call.name)
                case events.ApprovalNeededEvent(request_id=key):
                    await session.approvals.answer(key, ApprovalAnswer.DENY)
                case events.TurnFinishedEvent(result=result):
                    print("Turn finished:", result.stop_reason, result.completed)
```

Ignore event variants the client does not display. For a UI, clear outstanding
activity in a `finally` block on stream failure, cancellation or disconnect;
`TurnFinishedEvent` is emitted on normal stream exhaustion and is not guaranteed
on an error. Request/attempt/compaction completion alone must not mark the turn
complete. `RunResult.completed` describes successful task completion separately
from the end of the stream.

| Wire tag | Client correlation and persistence |
| --- | --- |
| `compaction.started`, `compaction.finished` | Live-only pair keyed by `compaction_id`; finish outcome is `applied`, `discarded`, `failed` or `cancelled` |
| `compaction.applied` | Durable history change; its `id` matches the live pair, and an applied record precedes the successful finish |
| `model.requested`, `model.request.failed` | Durable records keyed by `request_id`; one invocation closes with an assistant message or request failure |
| `model.attempt.started`, `model.retry.scheduled`, `model.attempt.finished` | Live-only diagnostics; separate groups by `retry_kind` and `retry_group_id`, then by `attempt` when known |

`RetryKind`, `RetryReason`, `ModelAttemptOutcome` and `CompactionOutcome` are
available from `harnessed.events`. Provider attempts share one Harnessed
`request_id`; context recovery and output correction create new request IDs in
their respective retry groups. Do not add counters across kinds. `attempt` is
one-based, `retry_count=attempt-1`, and `max_retries` excludes the initial attempt.
Unknown provider counters remain `None`. A finish may lack an observed start;
a scheduled retry may never start if cancellation occurs during backoff.

Reopening restores durable request correlation and applied history, without
replaying transient activity. Session-wide observers can use
`session.subscribe(transient=True)`. Model adapters report only observed SDK
facts through `ModelChunk(diagnostic=...)` or awaited
`harnessed.models.report_model_diagnostic(...)`; the PydanticAI bridge does not
automatically instrument hidden SDK retries. See the
[model adapter contract](docs/guides/models.md#report-observed-sdk-attempts) and
[streaming guide](docs/guides/streaming.md#model-and-retry-activity) for retry scopes,
partial responses and cancellation.

## 0.4.0 (2026-10-29)

This release adds explicit system prompt composition, optional MCP server tools
and conditional tool availability. It includes breaking prompt API changes from
0.3.0; the session record format remains version **2**.

### Explicit system prompt composition

- Replace `Agent(instructions=...)`, `Section` and plugin `guidance` with
  `Agent(system_prompt=...)`, keyword-only `SystemSection` declarations and
  synchronous, configuration-dependent `@system_section` providers.
- Plain `Agent(system_prompt="...", plugins=[...])` stays simple: plugin sections
  append automatically through `Unplaced.APPEND`, in configured plugin and
  declaration order, independent of dependency setup order.
- `SystemPrompt(sections=..., exclude=..., format=..., unplaced=...)` accepts
  strings, individual references, nested documents and `PluginSections(plugin)`
  groups. One `PluginSections()` rest slot per complete prompt is resolved after
  all explicit placements and exclusions, even those appearing later or nested.
- `Unplaced.ERROR` checks complete placement. Open groups and rest slots also
  include future declarations; use individual references without open groups or
  slots to detect newly added sections. There is no DROP policy; exclusions are
  explicit. Duplicate, conflicting and foreign references fail before providers run.
- Add PLAIN (default), XML and MARKDOWN rendering with consistent ordering,
  optional titles, empty-section omission and XML escaping. Remove silent
  content-based deduplication.
- Files and async factories run once per session for configured system text.
  Runtime mounts default to `SectionDelivery.ERROR`; explicit `CONTEXT` owns guidance
  per activation and removes it on suspension or unmount. Provider/factory/setup
  failures roll back mount resources. `IGNORE` skips all section evaluation.
  Historical requests retain delivered guidance without restoring it on reopen.
  The internal rendered-text service is now SystemPromptStore.
- Built-in declarations are `filesystem.usage`, `shell.usage`, `spill.usage` and
  `skills.catalog`. They append by default; place or exclude them through their
  configured recipes. Inspection shows section order, provenance and exclusions.
- Put advice specific to one tool in its description; sections describe shared
  rules and workflows. Runtime delivery leaves system text stable, while new
  tools still change the tool catalog; cache reuse is not guaranteed.
- See the [context guide](docs/guides/context.md) and offline example
  `uv run python -m examples.32_system_prompts` for the new API.

### MCP client

- Add `harnessed[mcp]` and the explicit `harnessed.plugins.mcp.MCP` plugin with
  `http()` and `stdio()` factories, per-mount connections, tool selection,
  namespacing, schema validation, progress and existing approval/output policies.
- Add a typed external-tool registration boundary to core without an MCP
  dependency. Prepared calls retain their registration identity. Cancellation
  closes the mount's connection; calls are never automatically retried.
- Add an offline example using a real MCP subprocess and a guide covering
  ownership, filtering, auth, result mapping and cancellation boundaries.

### Conditional tools

- `@tool(enabled=...)` accepts a bool or a synchronous predicate with `Inject`
  parameters. Instance predicates bind to the tool's mounted plugin. Disabled
  tools disappear from model requests and return an unavailable result when
  called. Conditions are checked before policy/approval and again before execution,
  including after approval or resource waits, without following replaced tool
  registrations. Other tools in the plugin keep their normal availability.

### Upgrade from 0.3.0

1. Replace `Agent(instructions="...")` with `Agent(system_prompt="...")`.
   For instruction lists, use `SystemPrompt(sections=(...))`. Import
   `SystemPrompt`, `SystemSection`, `PluginSections`, `PromptFormat`, `Unplaced`,
   `SectionDelivery` and `system_section` from `harnessed` as needed; `Section`
   is no longer exported.
2. Replace `Section("name", "text")` with
   `SystemSection(title="Name", text="text")`. All section constructors and
   factories are keyword-only: use `SystemSection.from_file(path="AGENTS.md")`
   and `SystemSection.from_factory(factory=callback, title="Name")`. Section
   identity now follows declarations and configured recipes rather than names
   or text; equal content is no longer silently deduplicated.
3. Replace plugin `guidance` attributes or constructor overrides with named
   `SystemSection` declarations or synchronous `@system_section` providers.
   Providers may read recipe configuration, but cannot use injected services or
   per-mount state. Built-ins expose `filesystem.usage`, `shell.usage`,
   `spill.usage` and `skills.catalog`. Place or exclude those references through
   `SystemPrompt`; replace `guidance=None` with an explicit exclusion and replace
   `override=True` sections with an exclusion plus the replacement section.
4. Review runtime `session.use(...)` calls. Plugins with system sections now
   require `section_delivery=SectionDelivery.CONTEXT` to deliver guidance, or
   `SectionDelivery.IGNORE` to omit it. The default raises `ConfigError` before
   mounting such a plugin. Context delivery ends on suspension or unmount and
   is not restored from historical requests when reopening a session.
5. Keep existing version **2** session stores; no record format migration is
   required from 0.3.0. Review rendered prompts and application assertions after
   migrating, including any reliance on text deduplication. MCP and conditional
   tools are opt-in; existing tools remain enabled by default.

For a migrated composition with a file section and built-in tools:

```python
from harnessed import Agent, SystemPrompt, SystemSection
from harnessed.plugins import Filesystem, Shell

agent = Agent(
    system_prompt=SystemPrompt(
        sections=(
            "Inspect relevant code before editing.",
            SystemSection.from_file(path="AGENTS.md", missing_ok=True),
        ),
    ),
    plugins=[Filesystem("."), Shell(".")],
)
```

The built-in sections append automatically. See the
[context guide](docs/guides/context.md) for explicit placement, exclusions and
runtime delivery, and the [MCP guide](docs/guides/mcp.md) for the new optional
integration.

## 0.3.0 (2026-10-29)

This release adds optional OpenTelemetry tracing, explicit plugin lifetime
requirements and session startup validation. It includes breaking composition
changes from 0.2.0; the session record format remains version **2**.

### Added

- Install `harnessed[otel]` and import `OpenTelemetryTracer` from
  `harnessed.integrations.opentelemetry` to export runtime spans to Phoenix,
  Langfuse or other OTLP collectors. The plugin replaces the default `tracer`
  slot and uses an application-owned or global OpenTelemetry provider without
  taking over exporter configuration or provider shutdown.
- Traces cover turns, steps, model requests, tool execution, approval waits and
  compaction, with durable correlation IDs, token usage, reported costs and
  failure outcomes. Parallel tools inherit their own span context. Prompts,
  completions, tool arguments/results and exception messages are excluded by
  default; `capture_content=True` enables bounded content capture. Reasoning,
  opaque provider state and stack traces remain excluded.
- Add an offline tracing example and a
  [tracing guide](docs/guides/tracing.md) covering provider ownership, backend
  configuration, trace parentage and content capture.

### Breaking changes

- Plugin lifetime requirements now use explicit `needs=(...)` declarations.
  `Inject[T]` binds attribute and parameter values without inferring composition
  requirements from class attributes, setup, hooks or function plugins. Declare
  dependencies such as `class Feature(Plugin, needs=(Actions,))` alongside
  `actions: Inject[Actions]`; function plugins use `@plugin(needs=(Actions,))`
  and bundles use `Bundle("name", needs=(Actions,))`. These declarations control
  waiting, teardown and restart when providers disappear or change. Optional
  injection remains optional unless its service is explicitly listed in `needs`.
  Tool declarations retain their `ToolRegistry` registration requirement;
  call-scoped values such as `ToolContext` do not belong in plugin `needs`.
- Agent sessions now reject plugins still waiting for required services with
  `PluginDependencyError` before starting the runtime or resuming queued work.
  Validation runs after `Session`, `SessionLog` and other session services have
  been published and plugin setup has settled. The error's `.issues` tuple names
  all waiting recipes and preserves the identities of their missing service keys.
  Complete compositions need no changes. Supply the missing services, or set
  `Agent(..., plugin_validation=PluginValidation.WARN)` for deliberately deferred
  compositions. Import `PluginValidation` from `harnessed`; warning mode emits
  `PluginDependencyWarning` with the same structured issues. Both diagnostics are
  importable from `harnessed.errors`. Setup failures still propagate in either
  mode. Startup validation also applies to `Agent.run()`. A failed startup closes
  acquired resources without discarding persisted queued input.

### Plugin diagnostics

- `Context.plugin_diagnostics()` exposes immutable snapshots of waiting plugins
  in its shared runtime after `await ctx.settle()`, including nested installs,
  extended scopes and transitively blocked plugins. The standalone kernel's
  deferred dependency behavior and `Context.install(strict=...)` remain unchanged.
  Runtime plugin additions and later service changes retain their temporal
  lifecycle behavior.

### Upgrade from 0.2.0

Audit class attributes, setup parameters, hook parameters and function plugins
annotated with `Inject`. Declare every service that must control the plugin's
lifetime in `needs`, including services accessed through `ctx[key]`:

```python
from harnessed import Plugin
from harnessed.kernel import Inject


class Feature(Plugin, needs=(Actions,)):
    actions: Inject[Actions]
```

Here `Actions` is the application's service type. Previously, the annotation
alone inferred the lifetime requirement. Without explicit `needs`, a missing
required injection now fails resolution instead of suspending the plugin, and
provider replacement does not trigger a remount. Optional injections remain
optional unless their service is listed in `needs`. See the
[plugin guide](docs/guides/plugins.md) for function plugins and bundles.

Provide all required services before opening a session. Incomplete compositions
now raise `PluginDependencyError`, including through `Agent.run()`. If a plugin
deliberately waits for a service added later, set
`Agent(..., plugin_validation=PluginValidation.WARN)` and import
`PluginValidation` from `harnessed`. Inspect the error or warning's `.issues`
to identify waiting plugins and missing service keys. See
[startup validation](docs/guides/plugins.md#session-startup-validation).

Existing version **2** session stores need no format migration from 0.2.0.
Tracing is opt-in; install the model provider's extra separately, for example
`harnessed[openai,otel]`, and close the application-owned telemetry provider
after active sessions have closed.

## 0.2.0 (2026-10-29)

This release introduces breaking event API and session storage changes from
0.1.0. Back up existing sessions and start a new session store after upgrading.
See the [release overview](docs/releases.md) and the migration instructions below
before updating an application.

### Model lifecycle

- Supplied PydanticAI models now enter and exit their native resource context,
  just like models resolved from strings. Pass a native model directly to
  `Agent(model=...)`; an outer model context is optional. PydanticAI keeps shared
  clients open until the last active context exits and reopens them on reuse.
  Explicitly supplied HTTP and provider SDK clients remain caller-owned.

### Breaking changes

- Import built-in event classes directly from `harnessed.events`. The short-name
  namespace modules (`message`, `tool`, `turn`, `step`, `text`, `reasoning`,
  `approval`, `compaction`, `context`, `inbox`, `model`, `output`, `rule`, `session`
  and `spill`) have been removed without compatibility aliases.
- Concrete built-in event classes now have canonical names ending in
  `Event`. Their definitions, imports, type annotations and schema definition
  names use the same names. Update imports and any consumers of JSON Schema
  definition names. Tool lifecycle discriminator changes are listed below.
- Remove `ApprovalRequest` from root SDK imports. Approver callbacks now import
  and annotate `ApprovalRequestedEvent` from `harnessed.events`.
- Import event-related enums directly by their full names, such as `TurnOutcome`,
  `StepOutcome`, `InboxMode`, `ApprovalStatus` and `ToolArgumentsMode`.
- Construct model request and assistant message events with an explicit
  `request_id` keyword. Response usage carries that ID; compaction usage has
  no response ID. Old version 2 JSONL records remain readable with deterministic
  IDs derived during loading.

Common replacements:

| Previous API | New import from `harnessed.events` |
| --- | --- |
| `events.message.Assistant` / core `AssistantMessage` | `AssistantMessageEvent` |
| `events.message.User` / core `UserMessage` | `UserMessageEvent` |
| `events.tool.ExecutionFinished` / core `ToolExecutionFinished` | `ToolExecutionFinishedEvent` |
| `events.tool.Called` / `events.tool.Started` | `ToolCallRequestedEvent` |
| `events.tool.Completed` / `events.tool.Returned` / `events.tool.Finished` | `ToolCallFinishedEvent` |
| `events.tool.CallStarted` | `ToolCallGenerationStartedEvent` |
| `events.turn.Started` / core `TurnStart` | `TurnStartedEvent` |
| `events.turn.Ended` / core `TurnEnd` | `TurnEndedEvent` |
| `events.turn.Finished` / core `TurnFinished[T]` | `TurnFinishedEvent[T]` |
| `events.step.Started` / core `StepStart` | `StepStartedEvent` |
| `events.step.Finished` / core `StepEnd` | `StepEndedEvent` |
| `events.text.Delta` / core `TextDelta` | `TextDeltaEvent` |
| `events.reasoning.Delta` / core `ReasoningDelta` | `ReasoningDeltaEvent` |
| `events.session.StatusChanged` / core `StatusChanged` | `SessionStatusChangedEvent` |
| `harnessed.ApprovalRequest` / `events.approval.Requested` | `ApprovalRequestedEvent` |
| `events.turn.Outcome` | `TurnOutcome` |
| `events.tool.ArgumentsMode` | `ToolArgumentsMode` |

For the remaining events, use the full core class name with an `Event` suffix,
for example `CompactionAppliedEvent` or `ToolPolicyAppliedEvent`.

```python
from harnessed.events import TurnEndedEvent
from harnessed.kernel import hook


@hook(TurnEndedEvent)
async def on_turn(self, event: TurnEndedEvent) -> None:
    print(event.turn_id, event.reason)
```

`TurnEndedEvent` remains the durable turn-end record with a reason;
`TurnFinishedEvent[T]` remains the terminal UI stream notification with a
`RunResult[T]`. `ToolExecutionFinishedEvent` records execution before result
review; `ToolCallFinishedEvent` carries the final reviewed result.

### Tool lifecycle (breaking)

- Replace eight overlapping tool lifecycle events with five explicit phases:
  `ToolCallGenerationStartedEvent`, `ToolCallRequestedEvent`,
  `ToolExecutionStartedEvent`, `ToolExecutionFinishedEvent` and
  `ToolCallFinishedEvent`. Observe `ToolCallFinishedEvent` for the final outcome,
  including denied calls and synthetic recovery errors.
- Request and completion events are durable values also forwarded directly to
  the UI stream. Separate transient started/finished projections and the extra
  reviewed-result checkpoint are removed. The final result is written and
  flushed once, after review and output limiting, in model call order.
- `ToolCallFinishedEvent` contains `call: ToolCall` and `result: ToolResult`;
  `call_id` is available as a property of its call. Raw execution receipts remain
  separate and are never exposed as reviewed results during recovery.
- Replace `tool.called` / `tool.started` with `tool.call.requested`, and
  `tool.completed` / `tool.returned` / `tool.finished` with `tool.call.finished`.
  Generation previews use `tool.call.generation.started` instead of
  `tool.call.started`. Progress and execution tags retain their meanings.
- Session record format version **2** is now required. Version 1 logs are
  rejected explicitly; no migration or compatibility aliases are provided.
  Start a new session store when upgrading persisted applications.

See the [event guide](docs/guides/events.md) for lifecycle phases and the
[release history](docs/releases.md) for published versions.

### Request and execution migration

For applications already using the flat event API above, apply these additional
changes. Harnessed emits the new events itself; applications constructing audit
events manually must pass the matching request ID.

| Previous code or assumption | Required update |
| --- | --- |
| `ModelRequestedEvent(request)` | `ModelRequestedEvent(request, request_id=...)`; the keyword is required |
| `AssistantMessageEvent(message, ...)` | Add `request_id=...` from the request producing that reply; the keyword is required |
| Synthetic request IDs from counters or `len(log.requests)` | Read `event.request_id` on request, assistant and response usage events |
| Every response usage record can be correlated by position | Match `ModelUsageRecordedEvent.request_id`; compaction usage has `purpose=UsagePurpose.COMPACTION` and `request_id=None` |
| An assistant message is the only model completion signal | Also handle `ModelRequestFailedEvent`, matching the same `request_id` |
| Tool timeout always produces `ToolStatus.ERROR` | Handle `ToolStatus.TIMEOUT` (`"timeout"`) explicitly; `result.is_error` remains true |
| An aborted execution has no receipt | Expect `ToolExecutionFinishedEvent` with `ToolStatus.CANCELLED` (`"cancelled"`); repaired final call results also use this status when execution started |
| Event unions or exhaustive dispatch cover all durable tags | Add `model.request.failed` and `observations.included` |
| A model request is immediately followed by usage or reply data | Expect `ObservationsIncludedEvent` after each request, including `observations=()` |
| Nudge IDs found in prompt text imply delivery | Put IDs in `Observation.refs`; acknowledge matching source refs from `ObservationsIncludedEvent` only when `form is ObservationForm.FULL` |
| Derived observation message ID `observation:{turn_id}:{step}:{source}` | IDs now end in `:{form}`, where form is `full` or `summary`; use provenance events instead of parsing message IDs |

Model failure outcomes are `ModelRequestOutcome.FAILED` (`"failed"`),
`CANCELLED` (`"cancelled"`) and `CONTEXT_EXCEEDED` (`"context_exceeded"`). A
partially emitted response closes with `AssistantMessageEvent(interrupted=True)`;
it does not also emit a failure. Context-limit retries are separate attempts with
new IDs. Observe the execution receipt to close execution activity; keep
`ToolCallFinishedEvent` for the final reviewed call result.

Manual event producers should carry one ID through the whole attempt:

```python
from uuid import uuid4

from harnessed.events import (
    AssistantMessageEvent,
    ModelRequestedEvent,
    ModelUsageRecordedEvent,
)

request_id = uuid4().hex
requested = ModelRequestedEvent(request, request_id=request_id)
usage_record = ModelUsageRecordedEvent(usage, request_id=request_id)
reply_record = AssistantMessageEvent(message, request_id=request_id)
```

To simplify plugins, replace optional dependency fields in `PluginState` and
their setup assignments with `name: Inject[Service]` class annotations. Required
class, setup, hook and tool injections already infer `needs`; retain explicit
`needs=(...)` for dynamic `ctx[key]` lookups. Use `class Feature(Plugin,
provides=True)` instead of `ctx.provide(Feature, self)`. Keep manual `ctx.provide`
when publishing a different object. Inherited `provides=True` uses the class that
declared it; `provides=SomeKey` selects another key. Optional class injections
capture `None` or the available service at mount time and refresh on remount;
per-call injection continues resolving at invocation time.

Import `SessionLog` from `harnessed` instead of
`harnessed.core.session.session`. Existing version **2** JSONL logs written before
request IDs remain readable: loading derives deterministic `session_id:index`
IDs, and missing observation refs default to `()`. Standalone newly constructed
request/reply events require IDs. The version 1 rejection described above was
already introduced by the tool lifecycle change; this update keeps version 2.

### Added

- `Observation.refs` carries application IDs independently of prompt text.
  `ObservationsIncludedEvent` follows each submitted request and lists only
  retained observation messages, with their source, refs and `ObservationForm`
  (`FULL` or `SUMMARY`). Removed, edited or compacted messages are excluded;
  matching ordinary text never implies inclusion. Both metadata and inclusion
  events survive JSONL replay. `SessionLog.included_observations(request)`
  exposes the same provenance query.
- Model request, assistant reply and response usage events carry `request_id`.
  Each submitted attempt receives a fresh ID, including context-limit retries.
  `SessionLog.request(id)` retrieves its submitted snapshot; `request_finished(id)`
  reports whether it has a durable completion. Existing version 2 JSONL records
  without IDs derive stable `session_id:index` identities when loaded.
- Every submitted model request closes with an `AssistantMessageEvent` (including
  interrupted partial replies) or `ModelRequestFailedEvent`. Failure outcomes
  distinguish `FAILED`, `CANCELLED` and `CONTEXT_EXCEEDED`.
- Tool execution starts now receive exactly one execution receipt on cancellation
  and timeout as well as success or error. `ToolStatus.CANCELLED` and
  `ToolStatus.TIMEOUT` identify those outcomes. Resume repair closes orphan model
  requests and tool executions before repairing the turn.
- Export `SessionLog` from the root SDK for plugin injection and log access.
- Class `name: Inject[T]` dependencies resolve before setup on each mount.
  Required dependencies infer `needs`; optional dependencies receive `None`
  when absent. Recipe and disposed-instance access raises `RuntimeError`.
  Deferred annotations compile once per class, including private attribute names.
- `Plugin(..., provides=True)` class metadata publishes the mount under the
  declaring class, inherited unchanged by subclasses. `provides=SomeKey`
  chooses another service key; `provides=False` disables inherited publication.
  Strict installation checks closed declared cycles before running setup.
- `@tool(description="...")` supplies an explicit model-facing description while
  leaving the method's docstring available for developer documentation. An
  explicit string wins over the docstring, including `""` to suppress it.
  `None` retains the existing `inspect.getdoc` fallback. The tool registry accepts
  the same option, and parameter descriptions continue to use `Field` metadata.
