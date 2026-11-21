# Repository guidance

## Annotations and documentation

Use `from __future__ import annotations` only when a module needs deferred names,
such as forward references or types imported under `TYPE_CHECKING`. Python 3.12+
already supports the native generic syntax; that alone needs no future import.
Prefer `Self` for constructors returning the current class. Avoid boilerplate
module docstrings that restate the filename. Document public behavior and useful
invariants where readers need them.

## Imports

Use absolute `harnessed` imports when crossing package boundaries. Parent-relative
imports (`from ..` and `from ...`, including deeper forms) are not allowed.
Single-dot relative imports within the same package remain allowed.

```python
from harnessed.plugins.shell import LocalShell, ProcResult, ShellBackend
from harnessed.kernel import Scope, Inject, hook
```

Plugin implementations and examples must use the public `harnessed.kernel` facade
for kernel APIs, such as `Scope`, `Inject`, and `hook`. Do not import
these through `harnessed.kernel.scope`, `.di`, or `.plugin`. Keep the facade's
exports in `harnessed/kernel/__init__.py` stable when reorganizing kernel internals.
The deliberate container rename to `Scope`/`ScopeClosed` is an accepted exception;
do not add compatibility aliases in the kernel or root facade.
Kernel implementation modules may use sibling imports to avoid import cycles.

Keep imports pointed at their owning layer. From lowest to highest, the layers
are the standard-library-only `kernel`, model contracts and errors, the event
vocabulary, the execution engine, optional features, and the application SDK.
The engine includes `runtime`, `context`, `children`, `approvals`, `output`,
`sessions`, `tools`, `prompts` and event stream and persistence modules. Features never import another plugin.
Only `models/pydantic_ai/` imports PydanticAI; only `plugins/skills.py` imports
YAML. Internal modules import their owning layer, not the root facade.

Follow `docs/reference/sdk.md`, the topic guides and the executable `smelt.yaml`
policy. Its rationale lives in `docs/contributing/smelt.md`.
Future scope belongs in `docs/contributing/roadmap.md`; those proposals are not
implemented API contracts. Subagents use isolated, parent-owned child sessions
through a runtime factory protocol. MCP and OTel are explicit optional integrations
with their own dependency extras.
Do not restore `core`, `services`, `seams`, `adapters`, `app`, `llms` or
`presets`, or add native provider clients. Import exported model, event, session
and tool types through their package facades. Import internal symbols from
their canonical modules. Keep
`harnessed.events.stream` and `harnessed.events.registry` explicit so the event
vocabulary can load independently. Service types live with sessions, prompts,
tools or the feature they extend. Applications inspect tools via `session.tools`.

Ruff enforces the parent-relative import rule with `TID252` and
`ban-relative-imports = "parents"`. Run `uv run ruff check .` before committing;
the same rule also runs through prek and CI.

Keep functions within Ruff's complexity limits (`C90` at 12, `PLR0911`,
`PLR0912`, `PLR0915`). Split long orchestration into named steps on its owner
instead of raising a limit or adding a suppression. Pass boolean options by
keyword (`FBT`), remove unused parameters unless a protocol or `@override`
requires them (`ARG`), and reach another object's private state only through
its public operations (`SLF001`); the kernel's cooperating classes are exempt.

Keep production imports at module scope. Ruff enforces `PLC0415`; the explicit
exceptions are `models/pydantic_ai/` (optional SDK) and `plugins/skills.py`
(optional YAML), which delay their extra's imports. Test-local
imports used for optional integration and import-failure tests are also exempt.
Do not add local imports to work around dependency cycles; move shared types or
change the dependency direction instead.

## Python style and boundaries

Keep public and cross-module signatures fully annotated, including return values
and collection element types. Contain `Any` and reflection at genuinely dynamic
boundaries such as schema construction and heterogeneous registries. Do not use
`Any`, `getattr`, or casts to hide a missing domain contract.

Prefer `StrEnum` for closed domain choices, states, outcomes and policies. Event
and message discriminators retain a concrete `Literal[Enum.MEMBER]` so unions
remain discriminated. Custom plugins may define their own discriminator enums.
Use stable dot-separated event tags, such as `assistant.message`, `tool.progress`
and `step.ended`. JSON-schema keys remain ordinary strings.

Plugins inherit the small `Plugin` base and declare Python tools as `@tool` methods.
Schema-defined remote tools use `ToolRegistry.register_external()` as an explicit
integration boundary, with validation in the owning integration.
Use module-level configuration models. Class-level `needs=` and `slot=` metadata
are supported; do not introduce a second decorator-based plugin class system.
The filesystem backend protocol is `FileSystemBackend`, its local implementation
is `LocalFileSystem`, and the application plugin remains `Filesystem`.

Place `logger = logging.getLogger(__name__)` immediately after the imports.
Write independent attribute assignments on separate lines. Only package initializers and the
single-module public facades listed in `smelt.yaml` declare `__all__`, at the
bottom. Single-dot sibling imports remain preferred within a package;
cross-package imports stay absolute.

Give a helper shared across modules a public name instead of importing a private
`_name`; keep it out of facades when it is internal. Parameters beyond the third
positional one are keyword-only. Name fixed-shape records with a `NamedTuple` or
dataclass instead of `tuple[...]` types with three or more or repeated fields.
Bind awaited results to a local before passing them to another call. `StrEnum`
values are explicit strings, and private literal class constants live at module
scope.

Keep lifecycle ownership behind public kernel operations. Higher layers must not
call private Scope lifecycle checks or inspect private registration state.
Stateful turn and step orchestration belongs to typed runtime components, not
free functions receiving a Session as a pseudo-`self` argument.

There is no released storage/API compatibility contract yet. Keep one canonical
current format; remove legacy migrations and transitional aliases rather than
adding compatibility layers for unreleased formats. Retain an explicit format
version for future released changes.

Normalize plugin prompt metadata into typed `PromptContribution` values at the
agent boundary. `PromptComposition` renders these contributions. Tool scheduling
captures one `PreparedToolCall`; it must not silently follow replaced bindings.

Use `Scope.register()` for owned registrations and `RegistrationState.active`
for visibility; do not inspect private Scope state from higher layers. Public
event classes use full names ending in `Event`. The domain vocabulary is
exported by `harnessed.events` and stream completion by `harnessed.events.stream`. Feature/model facades
use plain imports and explicit `__all__`; only the SDK root stays lazy to preserve kernel isolation,
with its type stub checked against the runtime export table.

Output restoration matches a registered validator identity and its schema, never
JSON Schema alone. Use versioned `OutputContract` keys for persisted application
workflows and bump the key when Python validation behavior changes.

## Architecture and convention checks

`smelt.yaml` is the executable policy and `uv.lock` pins Smelt CLI 0.14.0.
Layer assignments live in `architecture.layers.<layer>.modules`, and package APIs
live under `architecture.packages`. Activate rules and set their limits in `rules`.
Shared conventions live under `style` and package exceptions under `style.overrides`.
Use canonical `family/finding` rule names in configuration. Inline suppressions
accept those names or their family codes.
Before editing, run `uv run smelt context <module-or-path>` to inspect its role.
After editing, run `uv run smelt check --changed --base origin/main --format json`.
Before finishing, run `uv run smelt check --show-hints --fail-on warning`,
`uv run ruff check .`, `uv run ruff format --check .`,
`uv run pylint harnessed examples scripts`, `uv run ty check --error-on-warning`
and the relevant tests. Prek and CI run the same static checks. Smelt also checks
concrete runtime module cycles; annotation imports still count for boundaries.
Unnecessary future imports and implicit truthiness defaults are errors. Review
`complexity/excess-attribute-depth` hints against ownership and public API contracts.
Document placeholder Protocol/abstract methods and the reason for broad exception
handlers. Expand nested conditional expressions into explicit branches while
preserving evaluation order. Duplicate functions, class names and repeated
attribute-narrowing assertions are errors. Use named durations for waits and
include units in new internal duration constants.
Store related envelope metadata as named records instead of parallel sequences.
Review `modeling/dataclass-field-mutation` hints against actual ownership.
Frozen dataclasses with shared collections must own a read-only snapshot.
Review `idioms/loop-invariant-collection` hints without changing guards, empty-loop
behavior or the timing of evaluation.
Name awaited results, eager comprehensions and built-in reductions of generators
before passing them to calls. Smelt checks private
member access across module boundaries; only cooperating kernel implementations
share an immediate-package boundary. Modules contain at most twelve private
top-level definitions; split cohesive responsibilities into packages while keeping
facades small. The PydanticAI, MCP and worker packages also enforce one-loop
comprehensions and at most two constructor decisions. Elsewhere, review these
hints for cohesive extraction, preserving generator laziness, constructor
validation and optional-import boundaries.

Fix errors in code; do not introduce a debt baseline. Review warnings in context,
especially public APIs that static usage analysis cannot see. Preserve the lazy
root SDK and the standalone standard-library kernel. A necessary exception must
name its specific invariant beside the code (`# smelt: ignore[TST002] -- reason`)
or in a narrowly scoped, reasoned config entry. Do not expose helpers just to
satisfy tests. Keep independent test oracles in test support, and use local
deadlines for async coordination. `tests.layout: none` permits the existing topic
groups and consumer typing contracts; it does not require moving tests.

See `docs/contributing/smelt.md` for rule selection, limitations and adoption feedback.
