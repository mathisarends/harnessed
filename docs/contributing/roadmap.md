# Roadmap

The [SDK reference](../reference/sdk.md) and topic guides describe implemented
behavior. The initial release includes plugin composition and per-mount state,
typed tools and hooks, fixed instructions and dynamic context, optional providers,
explicit conversation state and live controls, structured output, streaming and custom events,
approvals, skills, compaction, spill, tracing hooks and offline testing.
An optional [OpenTelemetry integration](../guides/tracing.md) now exports runtime
spans to Phoenix, Langfuse and other OTLP collectors.

The following additions remain outside the current release scope. They are
candidates for discussion, not committed APIs or release dates.

## Hook and library tracing

The public `harnessed.tracing` facade, ambient library tracing and opt-in hook
invocation spans are implemented; see the [tracing guide](../guides/tracing.md).
The neutral Kernel instrumentation boundary preserves the standalone Kernel's
dependency rules. The repository-root `change.md` retains follow-up design for
automatic hook source-location caching and optional duration filtering in the OTel export
pipeline, preserving parents of visible children. Those follow-ups remain
proposals rather than implemented API contracts.
Library function tracing via `@traced` and `record(...)`, including source
metadata cached at decoration, is implemented in the public facade.

## Conversation state follow-ups

Applications own persistence through `SessionState` or an explicit `SessionStore`;
see the [session guide](../guides/sessions.md). Stores now support checkpoints for
paused execution. Application-supplied blob storage for large checkpoints remains
a future candidate; current checkpoints include their resource bytes.

The optional [MCP client](../guides/mcp.md) now exposes tools over stdio and
Streamable HTTP. Resources, prompts, sampling, elicitation and live catalog
refresh remain future candidates.

## Subagents

[Subagents](../guides/subagents.md) support delegation until completion and owned
background tasks with spawn/status/send/wait/cancel controls, on-demand child reports,
isolated logs, routed approvals and cleanup. The default depth limit permits direct
children only; applications can explicitly raise it. The feature accepts a small core factory protocol rather than
importing the application-level `Agent` into its plugin.

Automatic cross-process continuation,
structured delegation outputs and shared tree-wide spending limits remain future
candidates. Existing per-turn token budgets are step-boundary limits.

## Skills

`allowed-tools` frontmatter is retained as metadata and currently grants no
permissions. Whether and how to enforce it remains a separate design question.
Applications can trigger skills through existing tools, steering or request hooks;
a dedicated user-invocation API is deferred until a concrete application needs it.
YAML parsing is already implemented through the optional `harnessed[skills]` extra.

## Application-level scope

UI applications, RAG and evaluation systems are not part of the initial SDK scope.
Concrete consumers should establish their requirements before adding framework APIs.

## Instruction factory callbacks

`Instructions.from_factory` requires an asynchronous callback returning text.
Configuration-only synchronous content is returned by `Plugin.instructions()`.
The proposal to also accept synchronous session-context factories remains
undecided. Supporting both would be a small convenience addition; it should retain
explicit typing and the existing once-per-session rendering semantics.

## Questions from tools

Tools can ask for approval mid-call with `ToolContext.approve`. Free-text
clarifying questions from inside a tool are deferred; today a tool returns its
question and the turn ends, so the user answers in the next run.

## Decisions that are already settled

- Tools are plugin methods, and loop policies are plugins. There is one public
  tool-registration path.
- `Agent.run()` is available. Synchronous tools, dynamic-context callbacks and
  approvers are supported; synchronous tools run in a worker thread.
- Applications compose their own agents from plugins. There are no built-in
  presets, legacy facade aliases or native provider clients to restore.
- SDK failures, including `ToolNameConflict` and `ContextBudgetExceeded`, derive
  from `HarnessedError` and are importable from `harnessed.errors`.
- Provider context windows come from native profile metadata or explicit
  configuration. There is no duplicate hard-coded model table.

Future work should build on these contracts rather than reintroducing superseded
API sketches. Historical planning and completed review findings remain in Git history.
