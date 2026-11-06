# SDK ergonomics tickets

These tickets come from an SDK ergonomics review of `origin/main` at `2a1047f`.
They are **proposals for review**. They are not implemented API contracts, and
the roadmap in `docs/contributing/roadmap.md` stays authoritative for scheduled
scope. When a ticket is accepted and implemented, delete its file in the same PR
and record user-visible changes in `Changelog.md`.

Each ticket is self-contained, so another agent can pick it up without the
review conversation. Every ticket lists:

- **Problem**, with evidence from `origin/main` (paths, counts).
- **Target API**, as the code an application or plugin author would write.
- **Scope**, the affected modules, tests and docs.
- **Steps**, an implementation order that keeps the repository green.
- **Acceptance**, the checks that must pass.
- **Decisions**, open questions the reviewer must answer before work starts.

## Status values

`proposed` → `accepted` → `in progress` → done (file deleted).
`rejected` tickets get a one-line reason and are then deleted.

## Index

| # | Ticket | Priority | Breaking | Depends on | Status |
|---|--------|----------|----------|------------|--------|
| 02 | [Consistent event tags and format v4](02-event-tag-consistency.md) | high | yes (log format) | 01 | proposed |
| 03 | [Stream surface for UIs](03-stream-surface.md) | high | alias rename only | 01, 04 | proposed |
| 04 | [Fewer event bases, plugin-owned events](04-event-bases-and-ownership.md) | medium | yes | 01 | proposed |
| 05 | [Approvals API ergonomics](05-approvals-api.md) | high | yes | – | proposed |
| 06 | [Public session views instead of `harnessed.core`](06-public-session-views.md) | high | no (additive) | – | proposed |
| 07 | [Rename kernel `Context` to `Scope`](07-kernel-context-to-scope.md) | medium | yes | 06 | proposed |

Tickets 01–04 belong together. Do 01 first because it is a pure move that later
diffs can build on. 02 and 04 are breaking, so ideally they ship in the same
release as one log-format bump.

## Acceptance baseline for every ticket

From `AGENTS.md`:

```sh
uv run smelt check --changed --base origin/main --format json
uv run smelt check --show-hints --fail-on warning
uv run ruff check .
uv run ruff format --check .
uv run pylint harnessed examples scripts
uv run ty check --error-on-warning
uv run pytest
uv run mkdocs build --strict
```

Breaking changes go under `## Unreleased` → *Breaking changes* in
`Changelog.md`, each with an upgrade note. Do not add compatibility aliases for
unreleased formats.

## Not yet ticketed (needs re-verification on current main)

These came up in the review at lower priority. Check that each still applies
before turning it into a ticket:

- `Filesystem` and `FilesystemTools` are asymmetric: `glob`/`grep` exist only on
  `Filesystem`.
- Duplicate shell plumbing across the shell plugin and its backends.
- `Session.run`, `submit` and `stream` repeat input normalisation and output
  handling.
- Model special cases leak into the runtime instead of staying behind the model
  protocol.
- `ToolCall.command` and `exit_code` are shell-specific fields on a core type.
