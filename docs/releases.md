# Releases and compatibility

Harnessed 0.x is alpha software. This page summarizes the latest release and the
compatibility policy. The [changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
is the complete version history and upgrade reference.

## 0.23.0 (Unreleased)

Override or omit a plugin's prompt block with `with_instructions()` and
`without_instructions()`. Use `PrepareCompaction` to preserve plugin handover
notes in a compacted conversation. See the [context guide](guides/context.md)
and [plugin handover notes](guides/compaction.md#add-a-plugin-handover-note).

**Upgrade.** Existing saved sessions need no migration. Update audit and
checkpoint readers before writers to handle the new `accepted_note_keys` field.
Use the new module paths in the [examples learning path](guides/examples.md).
See the
[0.22.0 upgrade checklist](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0220).

## 0.22.0 (2026-11-20)

`Agent` takes loop, context and approval settings directly. Plugins declare
explicit capabilities, workspace providers and stable action keys for approval
rules. Tools run by default unless the application configures restrictions.

**Upgrade.** Update agent configuration and approval rules. Export saved JSON
conversations as snapshots with `0.21.0` and import them into a fresh store with
`0.22.0`. Execution checkpoints now use format **4** and standalone audit
envelopes use version **7**. See the
[0.21.0 upgrade checklist](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0210)
for the migration steps.

## 0.21.0 (2026-11-19)

`OTLPTracing` adds session-owned OTLP/HTTP export through the optional
`otel` extra. `TurnEndedEvent.usage` exposes recorded turn totals, and
OpenTelemetry step and turn spans include token usage and provider-reported
cost when available. See the [tracing guide](guides/tracing.md).

**Upgrade.** Update the package version and opt in to session-owned export
where needed. Existing stored sessions need no migration. Upgrade standalone
audit readers before writers during a staged rollout. See the
[0.20.0 upgrade checklist](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0200)
for installation, tracing setup and historical usage records.

## 0.20.0 (2026-11-19)

The SDK now groups session, prompt, tool and event APIs by domain.
OpenTelemetry model spans use OpenInference attributes so Phoenix can display
messages, tool calls and images. The
[tracing guide](guides/tracing.md#model-messages-and-images) covers message
inheritance, image limits and content capture.

**Upgrade.** Update imports from `harnessed.services` and moved event and model
types. Update any consumers or filters of model-span content using the
[0.19.0 upgrade checklist](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0190).
Existing conversation state, checkpoints, JSON stores and audit envelopes can
be read directly.

## 0.19.0 (2026-11-17)

`ToolParameter(enabled=...)` makes individual `Annotated` tool parameters
conditional for each activation. The `StreamModel` hook lets plugins wrap or
replace the agent loop's model stream. See the [tools guide](guides/tools.md)
and [model stream hooks](guides/models.md#wrap-or-replace-an-agent-model-stream).

**Upgrade:** pass `eof` by keyword in direct Python calls to
`Shell.write_command`. Persisted formats remain at version **3** for
conversation state, checkpoints and JSON stores, and version **6** for audit
envelopes.

## 0.18.0 (2026-11-16)

Registry-wide call envelopes extend tool call envelopes to every selected tool,
including other plugins', MCP and external tools, without changing them. Mount
`CallEnvelope` from `harnessed.plugins`; invalid fields are dropped by default
and `EnvelopePosition.TRAILING` places fields after the tool's parameters.
`envelope_value(call, Model)` reads envelope values for UIs and other event
consumers. See [envelopes for every tool](guides/tools.md#envelopes-for-every-tool).

**Upgrade:** no code changes or storage migration are required from 0.17.0:
state/checkpoints/JSON stores remain **3**, audit envelopes **6**.

## 0.17.0 (2026-11-14)

Tool call envelopes add validated model-supplied call metadata to policy,
execution and result-review hooks while keeping it out of tool callbacks.
See [envelopes](guides/tools.md#tool-call-envelopes) for declarations and examples.
Runtime components have been split and the repository now uses Smelt 0.8.0.

**Upgrade:** direct calls to built-in rule/spill hooks now use keyword arguments
for injected services. Update automation for `tests.yaml` and `mkdocs.yaml`.
Public SDK import paths remain stable; some implementation attributes are now
private. The
[changelog upgrade checklist](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0160)
covers direct hook calls and internal imports. No storage migration is required
from 0.16.0: state/checkpoints/JSON stores remain **3**, audit envelopes **6**.

## 0.16.0 (2026-11-10)

This release implements the Compaction API designed in 0.15.1: replaceable
prompts, separate `llm` selection, typed summarizer requests and complete-step
retention across turn boundaries and restarts. Summary budgets are derived
automatically, with optional whole-input targets and strict text ceilings.
Lifecycle events expose captured sizes and occupancy metrics.

**Upgrade required:** old constructor arguments have no aliases and custom
summarizers use `CompactionRequest -> Summary`. State is now format **3** and
audit envelopes version **6**; older values are rejected without migration.
Checkpoints/JSON stores remain format **3**, containing the new state format.
See the [changelog migration table](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0151)
and the [compaction guide](guides/compaction.md) for the full contracts.

## 0.15.1 (2026-11-10)

This documentation-only patch release adds the
[Compaction API proposal](contributing/compaction-api.md), its rationale and
implementation checklist. The proposed `llm`, `prompt`, `keep_last_steps`, context
targets and event additions are future work, not newly available SDK features.
Use the [compaction guide](guides/compaction.md) for the implemented API.

Runtime APIs, dependencies and persisted formats are unchanged from 0.15.0;
existing applications require no changes. State remains format **2**,
checkpoints/JSON stores **3**, and audit envelopes **5**.

## 0.15.0 (2026-11-10)

This release adds declarative method registrations and scoped output decisions,
and fixes skill frontmatter parsing. No API or persistence migration is required
from 0.14.0.

- **Registrations:** `harnessed.kernel.registration(recipe)` attaches a reusable
  `Registration` to a callable while preserving its identity and signature. It
  composes with tools and hooks; dependencies and cleanup follow each activation.
  See [third-party declarations](guides/plugins.md#third-party-method-declarations).
- **Phased output:** `PrepareOutput` hooks can return `OutputDecision.DEFER` for
  working steps and compose with `next_()` to enforce the declared output contract
  at completion. Both native JSON constraints and the reserved answer tool follow
  the decision. See [structured output](guides/structured-output.md#apply-the-contract-only-after-work-finishes).
- **Skills:** names accept Unicode alphanumeric characters, with trimming and
  NFKC normalization. Unquoted YAML scalar values retain their literal strings;
  duplicate keys and unsafe tags still fail. See [skills](guides/skills.md).

Existing 0.14.0 applications need no changes. When adopting phased output, keep
completion and continuation rules in the owning plugin; deferring an output
contract alone does not keep a turn running. Persistence versions remain state
**2**, checkpoints/JSON stores **3**, and audit envelopes **5**.

## Compatibility policy

- Minor releases during 0.x may introduce breaking API or storage changes. Release
  notes identify these changes and any required upgrade steps.
- Patch releases within a minor line are intended for compatible fixes. Pin the
  exact package version for reproducible deployments and persisted workflows.
- Current conversation snapshots use state format **3**. Execution checkpoints
  and JSON-store envelopes use format **3**; standalone audit-event envelopes
  use version **6**. Unsupported versions fail explicitly without automatic
  migration. The changelog records formats used by earlier releases. Former JSONL
  session archives are not conversation snapshots.
- Back up application-owned state and older archives before upgrading.
  Application-defined events and versioned output contracts have their own
  validation requirements; see
  [custom events](guides/events.md) and
  [structured answers](guides/structured-output.md).

Python 3.12, 3.13 and 3.14 are supported on Linux, Windows and macOS. Provider
integrations are optional extras; the standalone kernel uses only the standard
library. Harnessed is distributed under the
[MIT License](https://github.com/mathisarends/harnessed/blob/main/LICENSE).

## Earlier releases

Use the upgrade checklist for each intervening release when updating an older
application. Published artifacts and dates are listed in
[GitHub releases](https://github.com/mathisarends/harnessed/releases).

| Release | Main change | Upgrade checklist |
| --- | --- | --- |
| 0.14.0 | Plugin activation scope, tool deadlines, MCP and tracing | [From 0.13.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0130) |
| 0.13.0 | Service contracts, messages, streams and persistence | [From 0.12.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0120) |
| 0.12.0 | Additive input and parent-owned subagents | [From 0.11.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0110) |
| 0.11.0 | Opt-in persistence and atomic commits | [From 0.10.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-0100) |
| 0.10.0 | Plugin instruction blocks | [From 0.9.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-090) |
| 0.9.0 | Plugin recipes and composition | [From 0.8.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-080) |
| 0.8.0 | Explicit conversation state | [From 0.7.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-070) |
| 0.7.0 | Model settings, image assets and background shell | [From 0.6.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-060) |
| 0.6.0 | Sandbox backends and MCP cleanup | [From 0.5.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-050) |
| 0.5.0 | Tool metadata, action chains and step summaries | [From 0.4.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-040) |
| 0.4.0 | Prompt composition, MCP and conditional tools | [From 0.3.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-030) |
| 0.3.0 | Tracing and explicit plugin requirements | [From 0.2.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-020) |
| 0.2.0 | Event API and storage contracts | [From 0.1.0](https://github.com/mathisarends/harnessed/blob/main/Changelog.md#upgrade-from-010) |

The [initial release](https://github.com/mathisarends/harnessed/releases/tag/v0.1.0)
introduced the agent SDK, reversible plugins and optional model integrations.
