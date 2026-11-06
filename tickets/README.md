# SDK ergonomics tickets

The seven numbered tickets from the SDK review at `2a1047f` are implemented and
their proposal files have been removed. The changes cover domain event modules,
consistent event tags and audit format v4, shared event roles and plugin-owned
events, UI stream delivery, approval policy, session tool inspection and public
services, and the kernel scope API.

Current contracts live in the SDK reference and topic guides. Upgrade notes are
in `Changelog.md`. Future scope remains in `docs/contributing/roadmap.md`; the
unverified ideas below are not implemented API contracts.

## Acceptance baseline

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
