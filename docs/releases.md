# Releases and compatibility

Harnessed 0.x is alpha software. This page summarizes the latest release and the
compatibility policy. The [changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
is the complete version history and upgrade reference.

## 0.15.0 (2026-11-09)

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
- Current conversation snapshots use state format **2**. Execution checkpoints
  and JSON-store envelopes use format **3**; standalone audit-event envelopes
  use version **5**. Unsupported versions fail explicitly without automatic
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
