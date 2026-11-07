# Releases and compatibility

Harnessed 0.x is alpha software. Its public API and persistence contracts are
still evolving before 1.0.

Detailed breaking changes and upgrade instructions are recorded in the repository
[changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md).

## Unreleased

### Breaking change: plugin packs

Rename `Kit` to `PluginPack`, `KitPromptPolicy` to `PluginPackPromptPolicy`
and `ResolvedKit` to `ResolvedPluginPack` in imports and type references.
Composition descriptions now expose `PluginDescription.pack_path` instead of
`kit_path`, including nested replacement and exclusion descriptions. The old
names have no compatibility aliases. Composition behavior and storage formats
are unchanged.

See the [upgrade instructions](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0120)
and [plugin pack guide](guides/plugins.md#publish-library-defaults-with-pluginpack).
Run `uv run python -m examples.36_plugin_packs` for the renamed offline example.

## Compatibility policy

- Minor releases during 0.x may introduce breaking API or storage changes. Release
  notes identify these changes and any required upgrade steps.
- Patch releases within a minor line are intended for compatible fixes. Pin the
  exact package version for reproducible deployments and persisted workflows.
- Conversation snapshots use an explicit state format, version **1** since 0.8.0.
  Unsupported versions fail explicitly. Earlier releases used JSONL session
  archives: version 1 in 0.1.0, version 2 in 0.2.0 through 0.6.0 and version 3
  in 0.7.0. These archives are not conversation snapshots and are not automatically
  migrated. The standalone audit-event envelope remains version **3**.
- Back up application-owned state and older archives before upgrading.
  Application-defined events and versioned output contracts have their own
  validation requirements; see
  [custom events](guides/events.md) and
  [structured answers](guides/structured-output.md).

Python 3.12, 3.13 and 3.14 are supported on Linux, Windows and macOS. Provider
integrations are optional extras; the standalone kernel uses only the standard
library. Harnessed is distributed under the
[MIT License](https://github.com/mathisarends/harnessed/blob/main/LICENSE).

## 0.12.0 (2026-11-04)

This minor release adds parent-owned subagent sessions and replaces
`session.context` with additive `session.add_context()` input. It includes a
breaking API change from 0.11.0. Conversation snapshots remain state format **1**,
execution checkpoints and JSON-store envelopes remain format **2**, and standalone
audit envelopes remain version **3**. No storage migration is required.

### Additive context input (breaking)

Use `await session.add_context("Include source URLs.")` to append a user-role
text contribution without starting a turn, waking an idle agent or invoking the
model. Await the method before dependent work; configured stores commit the
contribution before it returns. Repeated input creates separate messages.

The named `session.context.set(name, value)` and `.remove(name)` service is
removed without a compatibility alias. Additive contributions persist in retained
conversation history across turns and restoration and follow ordinary compaction.
Use `Agent(instructions=...)` for fixed rules and `ContextValue` plugins for
changing named facts. See [context](guides/context.md).

### Parent-owned subagents

`Subagents` from `harnessed.plugins` delegates explicit tasks to configured
specialists in isolated child sessions. `delegate` waits for a terminal receipt;
`spawn`, `status`, `send`, `wait` and `cancel` let the coordinator continue while
children work and explicitly request reports. All six tools require the plugin;
there is no additional dependency extra.
The default depth limit is one; applications may explicitly raise it.
Parent-owned lifetimes, routed approvals,
durable relationships and recorded child receipts keep delegations inspectable
through completion, cancellation and checkpoint recovery. See
[subagents](guides/subagents.md).

Children receive the explicit task and later guidance, with their own configured
model, instructions, plugins and store. They do not inherit the parent's history,
tools or approval grants. Keep an explicit `async with agent.session()` scope open
for background work: `Agent.run()` closes its session after one turn and cancels
remaining children. Restoring a parent marks unfinished child references
interrupted without reopening children or repeating external actions.

### Upgrade from 0.11.0

1. Replace `await session.context.set("constraints", "Include source URLs.")`
   with `await session.add_context("Include source URLs.")` for additive input.
   Move changing named facts to `ContextValue` plugins; there is no additive
   replacement or removal operation. Read retained contributions through
   `session.messages` or `session.snapshot().history`. The snapshot's `context`
   mapping continues to contain plugin-owned named facts.
2. Keep existing `SessionState` snapshots and `SessionStore` adapters; their
   format versions are unchanged. Back up application-owned state before upgrading.
3. Add `Subagents` only where delegation is needed. Configure specialist
   capabilities, parent approval handling and limits explicitly. Background
   children may request approval after the originating stream ends; configure a
   session-wide parent approver for that case. Limits and usage are documented
   in the [subagent guide](guides/subagents.md#limits-and-concurrency).

Run `uv run python -m examples.41_subagents` and
`uv run python -m examples.42_background_subagents` for offline delegation examples.
The [model guide](guides/models.md#choose-a-model) now starts with provider strings
and explains when explicit adapter or native provider configuration is needed.

## 0.11.0 (2026-11-02)

This minor release adds opt-in session persistence, atomic application-owned
projections, OpenTelemetry content filtering and explicit kit resolution.
Conversation snapshots remain state format **1** and standalone audit envelopes
remain version **3**. New execution checkpoints and JSON-store envelopes use
format **2**. Existing 0.10.0 applications need no API or storage migration.

### Opt-in session persistence and atomic commits

Use `Agent(store=JsonSessionStore(root=...))` or supply an application-owned
`SessionStore`, then reopen conversations with `agent.session(id=...)`.
Explicit `SessionState` continuation and the ephemeral default remain available.
Stored execution checkpoints use format **2**, separate from conversation snapshots
and former JSONL archives. They have no automatic legacy migration. Persistent
paused sessions survive closure; register output validators with `restore_outputs`
before resuming structured work. See [sessions](guides/sessions.md) for the repository
contract, revision conflicts, recovery and execution ownership.

Custom stores receive a typed `SessionCommit`.
Read `commit.checkpoint` for recovery and `commit.changes` for new, sequenced
events. Apply mapped domain changes and the checkpoint in one repository
transaction, returning only after commit. Stores without a domain projection may
ignore the changes. `StoredSession` still returns a checkpoint and revision.

The session owns one background writer that batches intermediate changes.
Input, model/tool intent, receipts, pauses and terminal results await committed
storage. `session.flush()` commits pending state, and closure drains the writer.
Live text/reasoning deltas and tool-argument previews remain transient; they never
enter checkpoints or commits. Tool progress is a durable intermediate event.

Revision checks prevent lost updates, but applications still coordinate execution
per conversation and own the store or pool lifetime. Recovery repairs interrupted
work without automatically repeating external tool actions.

### Trace content filtering

Use `ContentCapture.redacted(*secrets)` from
`harnessed.integrations.opentelemetry` to mask registered literal strings and
secret wrappers throughout captured conversation and tool content, before size
limits. Add a synchronous `content_filter=` for application transformations or
field omission. Replacements are also redacted; processing failures omit content
without failing the agent. Content capture and exception text remain opt-in.
See [tracing](guides/tracing.md) for the policy and its limits.

### Explicit kit resolution

`Kit.resolve()` returns frozen `ResolvedKit` recipes and their original
composition provenance without mounting plugins or rendering instructions.
`KitPromptPolicy.INCLUDE` retains kit prompt strategies; `OMIT` skips them
recursively before slot conflict resolution. Explicit prompt plugins and selected
recipes' instruction contributions remain intact. See
[kit resolution](guides/plugins.md#resolve-a-pack-into-flat-recipes).

### Upgrade from 0.10.0

1. Keep existing ephemeral agents and `SessionState` snapshots. The default
   remains in memory; persistence requires an explicit `store=` and reopening
   requires a retained conversation ID.
2. For persistence, choose the bundled `JsonSessionStore` or implement
   `SessionStore` from `harnessed.sessions`. Save `commit.checkpoint` and any
   projections of `commit.changes` in the same revision-checked transaction.
   Register `restore_outputs` when reopening pending structured work, then call
   `resume()` for a deliberately paused session.
3. For unreleased persistence prototypes from `main`, replace string payloads
   or checkpoint-only saves with `SessionCommit`, and load through
   `StoredSession.checkpoint` instead of `.payload`. JSON encoding belongs to
   the adapter. Back up prototype data and start a format **2** store; format
   **1** execution checkpoints and former JSONL archives have no automatic
   migration. These prototypes were not part of 0.10.0.
4. Enable trace capture with an explicit redaction policy when needed, and use
   `Kit.resolve(prompt=...)` when flattening or inspecting reusable kits.
   Existing tracing and agent composition remain available.

Run the offline examples with `uv run python -m examples.38_session_store`,
`uv run python -m examples.39_typed_session_store` and
`uv run python -m examples.40_atomic_session_store`. The
[changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
records the complete release scope.

## 0.10.0 (2026-11-01)

This minor release replaces instruction descriptors and placement policies with
complete plugin instruction blocks collected in explicit plugin-list order. It
includes breaking prompt and kit APIs from 0.9.0. Conversation snapshots remain
state format **1** and the standalone audit-event envelope remains version **3**;
no storage migration is required.

### Plugin instruction blocks (breaking)

Plugins now override synchronous `instructions() -> SystemPrompt` and return
actual content. The method uses recipe configuration; session-dependent content
still belongs in `Instructions.from_file` or `Instructions.from_factory` values.
The base method returns an empty document. `Instructions` remains a plain content
value, while descriptors, `@instructions`, `instruction_refs()`, `InstructionRef`,
`InstructionProvider`, `PluginInstructions` and `Unplaced` are removed.

Applications explicitly collect complete blocks with
`SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED)`. The `order` argument is
required. Blocks follow the final configured plugin list: nested kits expand at
their position, removed recipes contribute nothing and slot replacements retain
the inherited position. Dependency setup order does not affect prompt order.
Selected providers run once per agent after composition. Missing collection
raises `ConfigError` before providers run, including for an override returning
empty content. See [prompt composition](guides/context.md).

Pass caller text as `Agent(instructions="...")` and document configuration as
`prompt=SystemPrompt(...)` or `prompt=SystemPrompt.from_plugins(...)`.
`Agent(system_prompt=...)`, prompt `exclude=` / `unplaced=` policies and kit
fragments are removed. A kit can now own the collection strategy through
`Kit(prompt=...)`. Parent kit defaults override child defaults, explicit agent
prompts override kit defaults and competing siblings require a direct choice.
Caller text is prepended without replacing that strategy. See
[kit composition](guides/plugins.md#publish-library-defaults-with-pluginpack).

Built-in Filesystem, Shell, BackgroundShell, Spill and Skills expose the same
`instructions()` method; `.usage` / `.catalog` attributes are removed. Override
the complete method to change or omit guidance. The Python method name
`instructions` is reserved for synchronous prompt content; async providers and
tool-decorated instruction methods fail at class definition. Runtime
`SectionDelivery` remains explicit and evaluates the method once per mount
activation. `None` content parts and `None` entries in Agent/Kit plugin lists are
now accepted and skipped.

### Typed dispatch and contributor checks

`Context.dispatch` and `EventBus.dispatch` retain the event's result type through
synchronous or asynchronous `default=` terminals and generic hooks. Static
checking rejects incompatible terminal results; callback returns are not
validated at runtime.

Contributor checks now use locked Smelt CLI **0.4.0** and Pylint in prek and CI.
Run `uv run pylint harnessed examples scripts` alongside Ruff, ty and Smelt.
Smelt warnings fail checks while design review hints remain advisory. See
[development and CI](contributing/development.md).

### Upgrade from 0.9.0

1. Replace descriptors and `@instructions` with synchronous
   `instructions(self) -> SystemPrompt` methods. Keep plain `Instructions`
   content values and session factories inside the returned document.
2. Split `Agent(system_prompt=...)` into caller `instructions=` and document
   `prompt=`. Collect plugin blocks with an explicit
   `SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED)` strategy.
3. Replace instruction references, placement policies, built-in `.usage` /
   `.catalog` attributes and kit fragments with complete method overrides and
   kit `prompt=` defaults. Review final list order for policy and completion
   plugins; rename Python tool methods named `instructions`.
4. Review dispatch `default=` terminal types and keep runtime mount delivery
   explicit. Existing conversation snapshots and audit envelopes need no format
   conversion.

Run `uv run python -m examples.32_system_prompts` and
`uv run python -m examples.36_plugin_kits` for offline examples. The
[complete migration and example](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-090)
records all removed APIs and their replacements.

## 0.9.0 (2026-11-01)

This minor release adds typed plugin recipes, explicit instruction placement,
plugin kits, Pydantic tool parameter objects and multi-event hooks. It includes
breaking plugin, dependency and prompt APIs from 0.8.0. Conversation snapshots
remain state format **1** and the standalone audit-event envelope remains
version **3**.

### Plugin recipes and instructions (breaking)

Declare configuration once with `Plugin[MyConfig]` and pass a frozen model as
`config=MyConfig(...)`. Replace class-level `Inject[T]` attributes with
`name = dependency(T)` from `harnessed.kernel`. Required fields declare lifetime
requirements; optional fields capture a service or `None` and restart the
activation when its provider changes. Call-scoped `Inject` remains available.

Each activation reconstructs its recipe's explicit constructor arguments.
Constructors must be repeatable configuration code; acquire resources in setup
and register cleanup through Context. Direct tool calls require an active
instance. Use `harnessed.testing.mount_plugin()` to test a recipe with fake
services without an agent or model.

Prompt content is now `Instructions` / `@instructions`, with `InstructionRef`,
`InstructionProvider` and `PluginInstructions` replacing the previous section
types. The built-in renderer plugin is named `PromptRenderer`. Every selected
plugin instruction requires explicit placement or exclusion, including when
the application supplies a string prompt or no prompt. `Unplaced.ERROR` is the
default; `PluginInstructions()` and `Unplaced.APPEND` opt into remaining content.

Compose prompts as `SystemPrompt("Help with documents.", workspace.instructions,
"Answer concisely.")`. Content is positional; `format`, `exclude` and `unplaced`
remain keyword-only options. See the
[prompt composition guide](guides/context.md#plugin-instructions-are-collected-by-default).

### Library composition

Publish defaults with `Kit(name, plugins)` and customize them with
`kit.without(*types)` and direct slot overrides. Direct entries override
child-kit exports; conflicting sibling kits require a direct decision at their
shared boundary. Built-in agent plugins remain the final fallback.
`agent.describe()` exposes immutable resolved order, origins, replacements,
dependencies and exclusions.

Place `kit.instructions` or named kit fragments to preserve library-owned
instruction ordering through nesting and slot replacement. Prompts and event
declarations are collected after recipe selection: discarded recipes contribute
no metadata, and explicit references to them raise a configuration error.
See [plugin composition](guides/plugins.md#publish-library-defaults-with-pluginpack).

### Tools and hooks

- Use `@tool(params=MyParams)` to expose a Pydantic model's fields as top-level
  tool arguments and receive one validated object. Aliases, constraints,
  validators and default factories are preserved; input validation is strict
  and rejects extra fields. Injected services and `ToolContext` remain separate.
  See [parameter objects](guides/tools.md#pydantic-parameter-objects).
- Use `@hook(EventA, EventB, ...)` for a shared sync or async handler accepting
  every declared event. Dependency injection, ordering and owned cleanup apply
  to each listener. Observer and waterfall events cannot share a decorator.
  See [hook signatures](guides/tools.md#hook-signatures).

### Upgrade from 0.8.0

1. Replace `Config = MyConfig`, repeated config annotations and forwarded config
   keywords with `Plugin[MyConfig]` and `config=MyConfig(...)`. Keep models frozen.
2. Replace class-level `Inject` attributes with `dependency(Service)` fields.
   Keep call-scoped `Inject` and explicit `needs` for other lifetime requirements.
3. Rename section declarations to `Instructions` / `@instructions`, groups to
   `PluginInstructions` and the renderer plugin to `PromptRenderer`. Replace
   `SystemPrompt(sections=parts)` with `SystemPrompt(*parts)`. Place every selected
   instruction explicitly or omit it with `exclude=(recipe.usage,)`.
4. Move resource acquisition into setup and review deliberate sharing in custom
   `create_instance()` methods. Use `mount_plugin` for direct tool tests.
5. Review order-sensitive prompts and discarded recipe references. Resolve
   sibling-kit conflicts at their shared boundary and internal conflicts inside
   the owning kit. Inspect `agent.describe()` before opening a session.

Run `uv run python -m examples.36_plugin_kits` and
`uv run python -m examples.37_plugin_recipes` for offline examples. The
[complete changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
records the full API changes and upgrade details.

Contributor checks now use `uv run prek run --all-files` with the existing
`.pre-commit-config.yaml`. Replace installed pre-commit hooks once with
`uv run prek install --force`. See [development and CI](contributing/development.md).

## 0.8.0 (2026-10-30)

This minor release replaces automatic session archives with explicit
`SessionState` values and adds an application-owned OTLP/HTTP provider helper.
It includes breaking session, persistence and output-restoration API changes
from 0.7.0. Conversation snapshots use state format **1**; older JSONL archives
are not accepted or automatically migrated.

### Explicit conversation state

Continue with `agent.run(..., state=state)` or `agent.session(state=state)`.
Save `result.state.to_json()` through application-owned storage and load it with
`SessionState.from_json(payload)`. An idle `session.snapshot()` also exports
current working context. Snapshots include retained image and spill bytes,
provider replay data and the metadata needed for future compaction. Compaction
releases discarded context and runtime indexes; earlier snapshots and results
remain independent owners of their data.

Archive backends, reopening by ID, fork APIs, `restore_outputs` and paused
execution restoration are removed. Live controls remain available. Approval
grants and plugin counters belong to one live session. Complete audit histories
can be stored externally through subscriptions or hooks; the standalone
audit-event envelope remains version **3**.

### OpenTelemetry provider lifecycle

Use `otlp_provider()` from `harnessed.integrations.opentelemetry` after installing
`harnessed[otel]`. Open it once around sharing agents and sessions, then pass the
returned provider to `OpenTelemetryTracer(tracer_provider=provider)`. It creates
a batched OTLP/HTTP export pipeline without replacing the global provider.
Standard OpenTelemetry environment variables configure exporter settings,
resources and sampling; explicit arguments take precedence.

Close sessions before leaving the provider context. Shutdown runs outside the
event loop after normal work, setup errors, application errors or cancellation
and attempts to export pending batches; confirm delivery at the destination.
Content capture remains disabled by default. See the [tracing guide](guides/tracing.md)
and run `uv run python -m examples.29_tracing --otlp` with exporter configuration;
the default example still uses its offline console exporter.

### Upgrade from 0.7.0

1. Replace archive plugins and ID-based reopening with application-owned state
   storage. The application owns conversation keys, authorization, revisions,
   expiration, deletion and competing writers.
2. Pass `SessionState` explicitly and save the returned state after terminal turns.
   Back up older JSONL archives and their resource directories before upgrading;
   start new state-format-1 conversations or arrange an application-specific
   conversion. No automatic archive migration is provided.
3. Replace fork APIs with independent runs receiving the same snapshot. Restore
   only idle conversations at terminal turn boundaries; paused executions,
   pending work, approvals, processes and Python plugin objects are not restored.
4. Configure output validators or versioned `OutputContract` values on each new
   turn. Save complete audit histories externally if required; snapshots contain
   retained working context, and ordered `EventLogCodec` persistence is removed.
5. Existing OpenTelemetry providers continue to work. Adopt the optional helper
   only when the application needs it to own the shared export pipeline.

See [the session contract](guides/sessions.md),
[structured answers](guides/structured-output.md) and the
[complete changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
for detailed changes and upgrade steps.

## 0.7.0 (2026-10-30)

This minor release adds typed model settings, immutable multimodal observations,
session-owned image assets and opt-in background shell commands. It includes
breaking model, message and observation API changes from 0.6.0 and advances the
session record format from **2** to **3**.

Observations become immutable text/image snapshots delivered automatically through
the existing `PreStep` and `session.observe()` APIs. They stay unchanged between
model calls and are removed with their steps at compaction boundaries. An explicit
`LatestObservationOnly()` policy can instead retain one snapshot per source.

Message classes are named `SystemMessage`, `UserMessage`, `AssistantMessage` and
`ToolMessage`. Replace `Observation(full=..., summary=...)` with
`Observation(content=...)`, remove `max_summaries` from `LatestObservationOnly`,
and use inclusion `refs`, `turn_id`, `step` and `message_id` instead of
`ObservationForm`. See [automatic observations](guides/context.md#steering-observations-and-retrieval).

The model and observation APIs share session format **3**.
Back up older stores and start a new one; versions 1 and 2 are rejected without
automatic migration. The [changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
contains the upgrade steps and storage limits.

### Model configuration and stored state

- Add immutable `GenerationSettings` on models and typed per-turn overrides.
  Move `max_tokens` out of `AgentLoop` and `ModelRequest` into settings; expose
  JSON provider-specific options through `provider_options`.
- Keep model selection on the configured `Model`. Remove `ModelRequest.model`
  and record the selected `ModelInfo` on `ModelRequestedEvent.model`.
- Require `PydanticAIModel(native_model)` when passing a native model to `Agent`.
  Custom model implementations now expose `settings: GenerationSettings`.
- Replace raw assistant provider dictionaries with `ProviderState`, including
  format/version and provider/model identity. Clear state with `None` when
  deliberately discarding signed replay data; incompatible bindings fail explicitly.

### Background shell commands and cleanup

- Compose the explicit `BackgroundShell` plugin with `Shell` or a registered shell
  backend to start commands, inspect live output, write stdin/EOF and terminate
  process trees. Completion queues a session-inbox notification and wakes an idle
  agent. Jobs belong to one open session and are not restored on replay or fork.
  See [background shell commands](guides/background-shell.md) and offline example 35.
- Concurrent `session.aclose()` calls wait for one shared driver/resource cleanup.
  Cancelling a caller does not interrupt cleanup for other callers. `SessionClosing`
  releases live resources once, including background shell processes.

### Image assets and session storage

- Store inline images once per session as typed `ImageReference(AssetRef(...))`
  values. JSONL histories keep references; binary data lives under `<id>.assets/`.
  Only selected images are resolved for the provider after compaction and budget checks.
- Use `session.assets` to read and write image bytes. Reopening histories leaves
  images on disk until needed; reads verify size and SHA-256, forks copy referenced
  assets and session deletion removes its asset namespace. Compaction retains audit assets.
- Custom `SessionStore` implementations now provide `assets(session_id)`.
  See [image assets](guides/sessions.md#image-assets-and-memory).

### Upgrade from 0.6.0

1. Replace raw model/run settings dictionaries with `GenerationSettings` from
   `harnessed.models`. Move `max_tokens` from `AgentLoop` and defaults from
   `ModelInfo` to the model's settings. Custom models now expose immutable
   `settings: GenerationSettings`; native PydanticAI objects require an explicit
   `PydanticAIModel(...)` wrapper. String model names remain supported.
2. Remove `model` and `max_tokens` from manually constructed `ModelRequest` values.
   Read model identity from `ModelRequestedEvent.model`. Request hooks no longer
   select a model by editing a request; replace the `Model` service for subsequent
   steps. Configure `ModelSummarizer` with the desired model directly.
3. Replace raw assistant replay dictionaries with `ProviderState`, or clear them
   explicitly with `None` when discarding signed replay data.
4. Import `SystemMessage`, `UserMessage`, `AssistantMessage` and `ToolMessage`
   in place of the old short names. Pass one immutable text/image `content` value
   to `Observation`, remove `ObservationForm` and `max_summaries`, and use final
   inclusion refs and explicit turn/step/message identities.
5. Add `SessionStore.assets(session_id)` to custom storage plugins. Recorded image
   content can now contain `ImageReference`; resolve bytes through `session.assets`
   or use `await session.log.resolve_request(request)` for manual provider replay.
6. Back up existing session stores, including their `.assets` and `.files`
   directories, and start fresh stores. This release rejects
   record versions **1** and **2**, without automatic migration. Update event
   consumers for the selected model identity and typed generation settings.
7. Opt in to background tools explicitly and keep their session context open
   while jobs are needed. `agent.run(...)` closes its temporary session on return.

The [changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
contains the detailed changes and a model-settings upgrade example.

## 0.6.0 (2026-10-30)

This minor release adds explicit Docker and Bubblewrap sandboxes and hardens MCP
transport failures and cleanup. It includes breaking shell/filesystem backend
changes from 0.5.0; the session record format remains version 2.

### Highlights

- Compose `DockerSandbox` or `BubblewrapSandbox` with `ShellTools()` and
  `FilesystemTools()` for a shared isolated workspace, resource controls,
  bounded transfers and explicit binary exports. Networking is disabled by
  default. Native runtimes and images/rootfs are application-managed; there is
  no new Python extra or fallback to local execution. See
  [sandboxes](guides/sandboxes.md).
- Resolve typed `shell` and `file_system` handles through
  `sandbox.services(session)`. Custom `SandboxBackend` implementations expose
  these services after asynchronous readiness, without implementing the internal
  worker protocol. Shell deadlines include queueing and launcher startup.
- Use the full filesystem tool set through `FilesystemTools`. Backends support
  bounded binary reads and `grep()`; built-in sandboxes batch each search into
  one worker request. Oversized requested file ranges fail explicitly, and
  invalid UTF-8 raises `UnicodeError`.
- Failed MCP connections close promptly, hide their tools, wake pending calls
  and drain owned tasks. HTTP session termination has a five-second network
  inactivity timeout. Ordinary tool `isError` results keep the connection usable;
  failed operations are never automatically retried. See
  [MCP tools and deployment boundaries](guides/mcp.md#deployment-boundary).
- Expand real-socket MCP HTTP and stdio tests, and exercise native Docker in CI
  on Linux/Python 3.12. Bubblewrap runtime tests require an explicitly configured
  host. Run the offline [sandbox example](guides/examples.md).

### Upgrade from 0.5.0

1. Move `env` and `max_output_bytes` from `ShellTools` to the shell backend or
   sandbox. `Shell` accepts these options only for its owned local backend;
   combining them with `implementation=` raises `ValueError`.
2. Treat `ShellTools(cwd=...)` as a backend-relative path. Sandboxes require paths
   relative to their workspace, without absolute host paths or `..` components.
   Custom shell backends must include queueing and startup in request deadlines.
3. Add `read_bytes()` and `grep()` to custom `FileSystemBackend` implementations;
   `harnessed.plugins.filesystem.search_files` is an optional grep fallback.
   Preserve character-based text ranges and explicit byte-limit failures.
4. Review allowlists and prompts for `FilesystemTools`, which now includes
   discovery, editing, deletion and `usage` guidance. Runtime mounts must choose
   `section_delivery` explicitly. Tool approval policies continue to apply.
5. Keep existing version 2 session stores. Export sandbox results before closing
   the session; reopened sessions and forks create fresh sandbox workspaces.
   Replace failed MCP connections instead of reusing their closed mounts.

The [changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
contains the full backend migration requirements. Existing local `Shell` and
`Filesystem` configurations need no sandbox integration.

## 0.5.0 (2026-10-30)

This minor release adds tool metadata, action-chain boundaries, reserved final-step
summaries and correlated compaction/model activity. It extends the event and hook
contracts from 0.4.0; the session record format remains version 2.

### Highlights

- Attach typed, process-local `metadata=(...)` to tools and inspect captured
  `ToolInfo` in policy, execution and result-review hooks. Metadata also works
  for schema-defined external tools and is neither sent to the model nor
  automatically persisted. See [tools and hooks](guides/tools.md).
- End the remaining action chain in a model response with
  `ToolChain.STOP_AFTER` or `ToolResult(..., break_chain=True)`. Unstarted calls
  receive `ToolStatus.SKIPPED`; already-started parallel work finishes normally,
  and durable break signals survive recovery. See
  [action-chain boundaries](guides/tools.md#end-an-action-chain).
- Inspect effective step progress in `PreStep` and optionally reserve the last
  available step with `AgentLoop(final_step=FinalStepPolicy.SUMMARIZE)`.
  The summary cannot execute tools and remains an incomplete text result within
  the configured limit. Resolved limits and policy survive pause/reopen. See
  [step progress and summaries](guides/sessions.md#step-progress-and-a-reserved-summary).
- Observe compaction start/finish notifications and applied, discarded, failed
  or cancelled outcomes. Stream model requests, failures and typed retry activity,
  keeping provider attempts, context recovery and output correction separate.
  Adapter callbacks and chunks can report actual SDK attempts; hidden retry
  counts remain unknown. See [streaming](guides/streaming.md#model-and-retry-activity).
- Use the [event reference](reference/events.md) for durable/transient delivery,
  correlation and completion contracts, and run the offline
  [basic-stream example](guides/examples.md).

### Upgrade from 0.4.0

1. Supply the effective keyword-only `max_steps=` when constructing `PreStep`
   manually. Hook consumers can read `remaining_steps`, `is_last_step` and
   `is_final_step` directly.
2. Extend exhaustive event handlers for compaction and model activity, and handle
   `ToolStatus.SKIPPED` and `ToolResult.break_chain`. Request/attempt completion
   does not imply turn completion. Preserve unknown retry counters as `None`.
3. Keep existing version 2 session stores; no format migration is required.
   Update strict application schemas for the additional defaulted model-request,
   turn-start and tool-result fields. Older readers may reject 0.5.0 records.
   Paused older turns without a resolved step limit use the reopening agent's
   configuration.
4. Existing tool chains and step-limit behavior remain the defaults. Opt in to
   metadata, chain boundaries and final summaries as needed. A reserved summary
   returns `StopReason.MAX_STEPS` with `completed=False`; for `output=T` runs,
   use `.text` because `.output` raises `OutputUnavailable`.

The [changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
contains the detailed changes, upgrade instructions and client integration
examples for tool metadata, final summaries and stream consumers.

## 0.4.0 (2026-10-29)

This minor release adds explicit system prompt composition, optional MCP server
tools and conditional tool availability. It includes breaking prompt API changes
from 0.3.0; the session record format remains version 2.

### Highlights

- Compose fixed system text with `Agent(system_prompt=...)`, keyword-only
  `SystemSection` declarations and synchronous `@system_section` providers.
  Plain text automatically appends plugin sections in configured order.
  `SystemPrompt` adds exact placement, nested documents, plugin groups, explicit
  exclusions and PLAIN, XML or MARKDOWN rendering. Equal text is retained rather
  than silently deduplicated. See the [context guide](guides/context.md).
- Runtime `session.use(...)` mounts explicitly choose how to deliver declared
  sections. `SectionDelivery.CONTEXT` owns guidance per activation and removes it
  on suspension or unmount; `IGNORE` skips section evaluation. The default
  `ERROR` rejects plugins with sections before mounting them. Configured system
  text remains fixed for the session.
- Install `harnessed[mcp]` and import `MCP` from `harnessed.plugins.mcp` to expose
  tools from MCP servers through `MCP.http()` and `MCP.stdio()` recipes.
  Connections and subprocesses belong to each session or runtime mount, with
  tool selection, namespacing, schema validation, progress and existing
  approval/output policies. See [MCP tools](guides/mcp.md).
- Declare `@tool(enabled=...)` with a bool or a synchronous predicate using
  `Inject` and mounted plugin state. Disabled tools are hidden from model
  requests and cannot execute. Conditions are checked again after approval and
  scheduling waits without following replaced tool registrations. See
  [conditional tools](guides/tools.md#conditional-tools).
- Run the offline examples for [conditional tools, MCP and system prompts](guides/examples.md).

### Upgrade from 0.3.0

1. Rename `Agent(instructions=...)` to `Agent(system_prompt=...)`. Wrap instruction
   lists in `SystemPrompt(sections=(...))`.
2. Replace `Section` with `SystemSection`, using keyword arguments such as
   `SystemSection(title="Role", text="...")`,
   `SystemSection.from_file(path="AGENTS.md")` and
   `SystemSection.from_factory(factory=callback, title="Catalog")`.
3. Replace plugin `guidance` attributes and constructor overrides with named
   section declarations or synchronous `@system_section` methods. Place or
   exclude the configured built-in references `filesystem.usage`, `shell.usage`,
   `spill.usage` and `skills.catalog` through `SystemPrompt`. To replace a section,
   exclude the original and place its replacement; `guidance=None` and
   name-based `override=True` are no longer prompt APIs.
4. Pass `section_delivery=SectionDelivery.CONTEXT` or `SectionDelivery.IGNORE`
   to runtime mounts of plugins that declare sections. Delivered runtime guidance
   is not restored on reopening a session; historical requests retain it for
   inspection.
5. Keep existing version 2 session stores. Review rendered prompts and tests for
   the new composition and removal of text deduplication. MCP and conditional
   tools are opt-in; existing tools remain enabled by default.

The [changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
contains the detailed changes and a migrated composition example.

## 0.3.0 (2026-10-29)

This minor release adds optional OpenTelemetry tracing, explicit plugin lifetime
requirements and session startup validation. It includes breaking composition
changes from 0.2.0; the session record format remains version 2.

### Highlights

- Install `harnessed[otel]` and use `OpenTelemetryTracer` from
  `harnessed.integrations.opentelemetry` to send runtime spans to Phoenix,
  Langfuse or another OTLP collector. The application configures exporters,
  sampling and provider shutdown. Core SDK imports remain independent of
  OpenTelemetry.
- Trace turns, steps, model requests, tool execution, approval waits and
  compaction with correlation IDs, usage, reported costs and outcomes. Content
  capture is disabled by default and bounded when explicitly enabled. See the
  [tracing guide](guides/tracing.md) and [offline example](guides/examples.md#tracing).
- Plugin composition now requires explicit `needs=(...)` declarations. `Inject`
  binds values without inferring lifetime requirements from attributes or
  parameters.
- Sessions validate plugin dependencies before runtime startup or queued work
  resumes. `PluginDependencyError` reports waiting plugins and missing service
  keys through structured `.issues`; `Context.plugin_diagnostics()` exposes
  dependency snapshots in the standalone kernel.

### Upgrade from 0.2.0

1. Audit plugins that inject services or access them through `ctx[key]`. Add
   lifetime requirements to the plugin class, `@plugin(needs=(...))` or
   `Bundle(..., needs=(...))` so missing providers suspend the plugin and provider
   changes restart it. Without `needs`, a missing required injection fails
   resolution and provider changes do not cause a remount. Optional injection
   remains optional unless its service is explicitly listed. See the
   [plugin guide](guides/plugins.md).
2. Provide all required services before opening a session, including through
   `Agent.run()`. For deliberately deferred compositions, explicitly select
   `Agent(..., plugin_validation=PluginValidation.WARN)` to emit
   `PluginDependencyWarning` and retain waiting plugins. Import the policy from
   `harnessed` and the error/warning from `harnessed.errors`. Complete compositions
   need no changes. Standalone kernel installation and later service changes keep
   their deferred lifecycle behavior. See
   [plugin startup validation](guides/plugins.md#session-startup-validation).
3. Keep existing version 2 session stores; this release introduces no session
   format change from 0.2.0.
4. To enable tracing, install the `otel` extra alongside any model provider
   extras and add `OpenTelemetryTracer` to the agent's plugins. Shut down an
   application-owned telemetry provider after active sessions have closed.

The [changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
contains the detailed changes and a plugin migration example.

## 0.2.0 (2026-10-29)

This minor release adds explicit event and request lifecycles, declarative plugin
injection and observation delivery provenance. It includes breaking API and
storage changes from 0.1.0.

### Highlights

- Import canonical built-in event classes directly from `harnessed.events`,
  with full names ending in `Event`. Five explicit tool lifecycle phases
  distinguish generation, requests, execution and the final reviewed result.
- Model requests, assistant replies and response usage share a `request_id`.
  Each request closes with an assistant message or `ModelRequestFailedEvent`;
  interrupted requests and executions are repaired during session recovery.
  Tool cancellation and timeout have explicit `ToolStatus` values.
- `Observation.refs` and `ObservationsIncludedEvent` identify which source
  observations reached each final submitted request, including full or summary
  form. Query the durable provenance through `SessionLog.included_observations`.
- Plugin class attributes annotated with `Inject[T]` resolve before setup on
  each mount. Required dependencies infer `needs`; `provides=True` or an explicit
  service key publishes the mounted plugin. `SessionLog` is exported by the
  root SDK for injection and application access.
- `@tool(description="...")` overrides the model-facing description while
  preserving the method docstring for developer documentation.
- Supplied native PydanticAI models enter and exit their resource context just
  like models resolved from strings. Explicitly supplied HTTP and provider SDK
  clients remain caller-owned.

### Upgrade from 0.1.0

1. Back up persisted sessions and start a new session store. Version 1 records
   are rejected by the version 2 codec; no automatic migration is provided.
2. Replace event namespace imports such as `events.text.Delta` with canonical
   imports such as `TextDeltaEvent` from `harnessed.events`. Replace the root
   `ApprovalRequest` import with `ApprovalRequestedEvent` from the same facade.
3. Update event dispatch and JSON Schema consumers for the new event names and
   tool tags. Observe `ToolCallFinishedEvent` (`tool.call.finished`) for the final
   reviewed result, including denied calls.
4. If constructing audit events manually, pass the same explicit `request_id`
   to request, reply and response usage events. Handle `model.request.failed`,
   `observations.included`, `ToolStatus.CANCELLED` and `ToolStatus.TIMEOUT` in
   exhaustive event and result dispatch.

The [0.2.0 changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
contains the full replacement table and request/execution migration examples.
See the [event guide](guides/events.md), [plugin guide](guides/plugins.md) and
[observation delivery guide](guides/context.md) for the current contracts.

## 0.1.0 (2026-10-29)

Initial public release, with the following capabilities:

- An asynchronous agent SDK with tools declared as plugin methods, reversible
  plugin lifetimes, dependency injection, hooks and replaceable defaults.
- Optional PydanticAI integrations for OpenAI-compatible providers, Anthropic and
  Google, including structured answers and typed streaming events for text,
  reasoning, tool argument previews and approvals.
- Append-only session logs, JSONL persistence, crash recovery, forks, queued input
  and pause/resume/stop controls. In-memory session retention can be bounded or
  disabled for one-shot work.
- Provider-native tools forwarded through PydanticAI, including provider-side web
  search and code execution where supported.
- Tool approvals, filesystem and shell tools, skills, context compaction, output
  limits and session-owned spill artifacts.
- An offline testing toolkit, executable examples, typed public APIs and
  [session memory and replay benchmarks](benchmarks/sessions.md).

Provider-native tools execute on the provider and bypass Harnessed tool policies
and approvals. Audio/video output and hosted resumable operations are not exposed
by the current model contract. Subagents, MCP and OTel integrations are deferred.
Provider tests use native SDK mocks and mock HTTP responses; they do not validate
live API availability.
