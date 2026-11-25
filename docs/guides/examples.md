# Executable examples

The examples form a learning path in three stages. Each stage is a package under
[`examples/`](https://github.com/mathisarends/harnessed/tree/main/examples), and
its numbers give the reading order within the stage:

1. **[Getting started](#getting-started)**: an agent, a tool and a stream.
2. **[Applications](#applications)**: approvals, sessions, context, subagents and
   operations, using the built-in plugins.
3. **[Plugin authors](#plugin-authors)**: tools, hooks, dependency injection,
   lifecycles and the kernel.

Run an example from the checkout with the locked uv environment:

```bash
uv run python -m examples.getting_started.01_tool_agent
```

The examples run offline with scripted models from `harnessed.testing`, and the
test suite executes them. `ScriptedModel` replays fixed replies; `call()` scripts a
tool call from a decorated method or tool name. Only the DeepSeek example needs
provider credentials, and only the MCP example needs an external endpoint.

## Getting started

| # | Example | Shows |
| --- | --- | --- |
| 01 | [Tool agent](https://github.com/mathisarends/harnessed/blob/main/examples/getting_started/01_tool_agent.py) | A `Plugin` with one `@tool`, a session and a turn result |
| 02 | [Plugins](https://github.com/mathisarends/harnessed/blob/main/examples/getting_started/02_plugins.py) | Plugin instructions and a one-shot `agent.run()` |
| 03 | [Basic stream](https://github.com/mathisarends/harnessed/blob/main/examples/getting_started/03_basic_stream.py) | Printing text deltas and reading the result afterwards |
| 04 | [Structured output](#structured-output) | A Pydantic result type with one corrected answer |
| 05 | [DeepSeek](https://github.com/mathisarends/harnessed/blob/main/examples/getting_started/05_deepseek.py) | A real provider with the workspace tools and approvals |

The DeepSeek example requires `uv sync --extra openai` and `DEEPSEEK_API_KEY`.

### Structured output

`uv run python -m examples.getting_started.04_structured_output` runs a complete
typed triage task offline, including one invalid answer and its correction. The
final output is a Pydantic model; the control tool is excluded from application
tool-call results. See [the response-contract guide](structured-output.md).

## Applications

| # | Example | Shows |
| --- | --- | --- |
| 01 | [Approvals](https://github.com/mathisarends/harnessed/blob/main/examples/applications/01_approvals.py) | Headless denial, remembered approvals, hook order and response timeouts |
| 02 | [UI stream](#ui-streaming) | Every event a user interface handles, including an approval |
| 03 | [Inbox](https://github.com/mathisarends/harnessed/blob/main/examples/applications/03_inbox.py) | Queued turns and quiet context updates |
| 04 | [Pause, resume and stop](https://github.com/mathisarends/harnessed/blob/main/examples/applications/04_pause_resume_stop.py) | Session control while tools run |
| 05 | [Parallel tools](https://github.com/mathisarends/harnessed/blob/main/examples/applications/05_parallel_tools.py) | Concurrent independent calls and resource keys for conflicting writes |
| 06 | [Background shell](https://github.com/mathisarends/harnessed/blob/main/examples/applications/06_background_shell.py) | Writing to, reading and terminating a running command |
| 07 | [Sandbox](https://github.com/mathisarends/harnessed/blob/main/examples/applications/07_sandbox.py) | Optional Docker or Bubblewrap workspaces |
| 08 | [Skills](#skills) | Folder and inline skills with resources |
| 09 | [MCP](#mcp-tools) | Tools from an existing MCP endpoint |
| 10 | [Tool output limits](https://github.com/mathisarends/harnessed/blob/main/examples/applications/10_tool_output_limits.py) | Truncating large results and reading in ranges |
| 11 | [Session spill](https://github.com/mathisarends/harnessed/blob/main/examples/applications/11_session_spill.py) | Storing full output beside a short preview |
| 12 | [Compaction policies](https://github.com/mathisarends/harnessed/blob/main/examples/applications/12_compaction_policies.py) | Ratio and turn-based compaction compared |
| 13 | [Context strategies](https://github.com/mathisarends/harnessed/blob/main/examples/applications/13_context_strategies.py) | Combining observations, spill and compaction |
| 14 | [Session state](https://github.com/mathisarends/harnessed/blob/main/examples/applications/14_session_state.py) | Snapshot export and independent continuations |
| 15 | [External state](https://github.com/mathisarends/harnessed/blob/main/examples/applications/15_external_state.py) | Application-owned persistence |
| 16 | [Session commits](https://github.com/mathisarends/harnessed/blob/main/examples/applications/16_session_commits.py) | A snapshot file and an append-only JSONL journal per session, with a persisted pause |
| 17 | [Typed documents](https://github.com/mathisarends/harnessed/blob/main/examples/applications/17_typed_documents.py) | Snapshots and journal entries as JSONB-style documents in a repository |
| 18 | [Atomic commits](https://github.com/mathisarends/harnessed/blob/main/examples/applications/18_atomic_commits.py) | Domain messages, journal entries and snapshots in one transaction |
| 19 | [Subagents](#subagents) | Delegating to an isolated child |
| 20 | [Background subagents](#background-subagents) | A child running while the coordinator continues |
| 21 | [Tracing](#tracing) | OpenTelemetry spans for a tool loop |
| 22 | [Reviewing approvals](#reviewing-approvals) | Review shell calls with a separate model and pass uncertain decisions to a UI responder |
| 23 | [Journal commits](https://github.com/mathisarends/harnessed/blob/main/examples/applications/23_journal_commits.py) | Append journal entries per commit and replace the snapshot once per turn |

### UI streaming

`uv run python -m examples.applications.02_ui_stream` shows text, reasoning, tool
progress, an acknowledged approval and a typed terminal report. It extends the
minimal [basic stream](https://github.com/mathisarends/harnessed/blob/main/examples/getting_started/03_basic_stream.py).
See [the stream contract](streaming.md).

### Skills

`uv run python -m examples.applications.08_skills` discovers a folder and inline
skill, loads instructions, and reads a resource without approvals or output
truncation. See [the Skills guide](skills.md).

### MCP tools

`uv run --extra mcp python -m examples.applications.09_mcp --url http://localhost:8000/mcp`
consumes an existing MCP endpoint with a read-only `search(query: str)` tool.
It discovers the remote tool and invokes it through a scripted agent, without
provider credentials. The endpoint must be supplied separately. See the
[MCP guide](mcp.md) for HTTP, stdio, filters and connection ownership.

### Subagents

`uv run python -m examples.applications.19_subagents` delegates a review to an
isolated child, prints its durable receipt and inspects tree usage with scripted
models. See [the subagent guide](subagents.md).

### Background subagents

`uv run python -m examples.applications.20_background_subagents` starts a child
without waiting, continues the coordinator, requests an explicit child status
report and waits for the final result. Scripted callbacks keep the example
offline; real models receive the same tools and report through `answer_status`
in their normal child turn.

### Tracing

`uv run --extra otel python -m examples.applications.21_tracing` exports a complete
tool loop to the console, with an outer backend span and model usage, without
network access or credentials. See [the Phoenix/Langfuse setup guide](tracing.md).

Add `--otlp` to use `otlp_provider()` with batched HTTP export instead. Configure
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and any authentication headers for your
destination first; the model remains scripted and requires no provider credentials.
The helper wraps all sessions and shuts down its provider on exit. The default
console mode remains offline.

### Reviewing approvals

`uv run python -m examples.applications.22_review_approvals` demonstrates an
allow, a denial and a handoff to a simulated human. Both models are scripted by
default and the shell backend records the commands it would execute.
Pass `--review-model provider:model` to use a configured model for review.
The proposed calls and command execution remain simulated in both modes.

## Plugin authors

| # | Example | Shows |
| --- | --- | --- |
| 01 | [Explicit composition](#explicit-composition) | Sharing plugin lists through functions, and slots |
| 02 | [Tool context](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/02_tool_context.py) | `needs()` for a backend and progress through `Inject[ToolContext]` |
| 03 | [Validated forms](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/03_validated_forms.py) | Pydantic tool arguments and validation feedback |
| 04 | [Conditional tools](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/04_conditional_tools.py) | Tools enabled by session state |
| 05 | [Conditional parameters](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/05_conditional_parameters.py) | Parameters enabled by plugin configuration |
| 06 | [System prompts](#system-prompt-composition) | Configuration-dependent prompt blocks and formats |
| 07 | [Class plugins](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/07_class_plugins.py) | A plugin owning its tools, services and hooks |
| 08 | [Failure nudge](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/08_failure_nudge.py) | Two cooperating hooks: reviewing results and preparing requests |
| 09 | [Rules](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/09_rules.py) | A stateful review hook compared with declarative rules |
| 10 | [Context](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/10_context.py) | A provided memory service, attachments and request preparation |
| 11 | [Observations](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/11_observations.py) | Replacing stale observations before each step |
| 12 | [Custom events](#custom-events) | Durable application events in the stream |
| 13 | [Plugin recipes](#plugin-recipes-and-dependencies) | Configuration, dependencies, events and `mount_plugin` tests |
| 14 | [Plugin lifecycle](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/14_plugin_lifecycle.py) | Provided services, activation-owned state, teardown and scope listeners |
| 15 | [Built-in tool lifetimes](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/15_builtin_tool_lifetimes.py) | Replacing a workspace backend in a running session |
| 16 | [Temporal needs](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/16_temporal_needs.py) | A dependency arriving and being replaced after a plugin starts |
| 17 | [Layered SDK](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/17_layered_sdk.py) | Plugin status following a service in a standalone `Scope` |
| 18 | [Models](https://github.com/mathisarends/harnessed/blob/main/examples/plugin_authoring/18_models.py) | Implementing the structural `Model` contract |

Examples 01 to 15 extend an `Agent`. Examples 16 and 17 use a standalone `Scope`
to show service lifetimes and temporal composition; example 18 implements a model
adapter.

### Explicit composition

`uv run python -m examples.plugin_authoring.01_explicit_composition` shares a
composition through an ordinary function returning plugins, configures the loop
with `AgentLoop`, prints the static description and shows a duplicate slot raising
`PluginConflict`. See [composition](plugins.md#share-compositions-with-ordinary-functions).

### System prompt composition

`uv run python -m examples.plugin_authoring.06_system_prompts` declares literal and
configuration-dependent plugin blocks, selects a collection format with `prompt=`,
and renders the same document as plain text, XML and Markdown. See
[system prompts and changing context](context.md).

### Custom events

`uv run python -m examples.plugin_authoring.12_custom_events` declares a durable
application event, includes it in a discriminated union, streams it and replays
its stored value. See [the event extension guide](events.md).

### Plugin recipes and dependencies

`uv run python -m examples.plugin_authoring.13_plugin_recipes` declares stable
required and optional dependencies, `Plugin[FileToolsConfig]` with an explicit
frozen config object and activation-owned state. It declares `FILE_EVENTS`, emits
a `DocumentReadEvent` through `ToolContext.emit`, and handles it in an independent
observer with `@hook`. A `workspace(...)` factory returns the plugin list the agent
mounts. It also tests the same recipe's configuration and fake dependencies with
`mount_plugin`, without an agent.
