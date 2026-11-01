# Contributing

Use Python 3.12–3.14 and the root-level `harnessed` package. Read `AGENTS.md`
for layer and import rules. Install the locked environment with
`uv sync --locked --all-extras` and hooks with `uv run prek install`.
If you previously installed pre-commit hooks, replace them once with
`uv run prek install --force`.

## Validation boundaries

Validate external values (user configuration, files, network payloads, and model
arguments) with Pydantic. Use frozen dataclasses for internal events and values.
Do not add repeated validation/reflection to token or tool dispatch paths.

Class plugins may define a module-level Pydantic configuration model and assign
it with `Config = MyPluginConfig`; the constructor validates its keyword
configuration. Annotate `config: MyPluginConfig` for static checking. Allocate
mutable per-mount state in `setup()`, not
in the recipe constructor. Register resource cleanup with `ctx.effect()` as
soon as allocation succeeds; these effects also run when setup fails. The
recipe's shared clients and locks are not deep-copied. Applications retain
ownership of shared client objects unless the plugin explicitly owns them.

Functional plugins should declare temporal composition with `needs=[...]` and
resolve those services through their context. Use `Inject` for per-call services
in tools/hooks. `Inject[T | None]` is optional and does not drive restarts.
For a named service use `Annotated[T, InjectMarker(key)]`.

Hook priorities run in ascending order: `Priority.GUARD`, `Priority.DEFAULT`,
then `Priority.OBSERVE`. Equal priorities keep registration order. Use explicit
`owner=ctx` when registration happens outside the plugin's activated call chain.
Duplicate tool or prompt-section names require `override=True`.

## Verification

Run `uv run prek run --all-files` and `uv run pytest` before submitting.
The hooks use the versions in `uv.lock`:

| Check | Responsibility |
| --- | --- |
| Ruff lint and format | Import sorting, indentation, whitespace, syntax and selected Python/async rules |
| Pylint | Control flow, collection mutation while iterating, renamed override parameters and final/async overrides |
| ty | Type checking, including the Python/platform matrix in CI |
| Smelt 0.4.0 | Layer/facade boundaries, runtime module cycles, definition ordering, necessary future imports, explicit optional defaults and test conventions |

Run the checks individually when investigating a failure:

```bash
uv run ruff check . --fix
uv run ruff format .
uv run pylint harnessed examples scripts
uv run ty check --error-on-warning
uv run smelt check --show-hints --fail-on warning
```

Ruff applies safe lint fixes and formatting. Smelt's optional-default rewrite
requires `--select SMT321 --fix --unsafe-fixes`: review whether each fallback
should apply only to `None` before using it. Do not remove future imports needed
for Python 3.12 forward references or `TYPE_CHECKING` imports.

Pylint deliberately complements the other tools with selected checks. Its general
call/type inference does not model the lazy SDK or injected lifecycle signatures;
ty checks those contracts. The intentional invalid calls in `tests/typing` belong
to ty rather than Pylint. Smelt warnings fail hooks and CI; design review hints
remain advisory and require contextual review, without a debt baseline.

The static consumer contracts in `tests/typing` run in ty alongside the library,
examples and scripts. CI checks all supported Python versions on Linux, Windows
and macOS, then builds and smoke-tests the installed wheel outside the checkout.
