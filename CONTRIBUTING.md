# Contributing

Use Python 3.12-3.14. Read [AGENTS.md](AGENTS.md) for repository conventions and
[development and CI](docs/contributing/development.md) for checks, docs maintenance
and the release process.

```bash
uv sync --locked --all-extras
uv run prek install
uv run prek run --all-files
uv run pytest
uv run mkdocs build --strict
```

## Design boundaries

Keep the standalone kernel standard-library-only and imports pointed at their
owning layer. Follow [Smelt policy](docs/contributing/smelt.md); inspect a module
with `uv run smelt context <module-or-path>` before editing it.

Validate external configuration and payloads with Pydantic. Keep constructors
repeatable, mutable activation state in `PluginState`, and resource cleanup in
`scope.effect()`. Declare required service fields with `needs()` and optional
fields with `observes()` from `harnessed.kernel`; use `Inject` for per-call
services. See [plugin recipes](docs/contributing/plugin-recipes.md) for ownership
and [plugins](docs/guides/plugins.md) for the public contract.

Keep documentation and examples in English. Put user explanations in topic guides,
API summaries in the reference and future proposals in the roadmap. Record release
changes and migrations in [Changelog.md](Changelog.md); keep the
[release overview](docs/releases.md) brief.
