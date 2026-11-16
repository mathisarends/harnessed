# Smelt policy and adoption feedback

Harnessed pins [`smelt-cli` 0.10.0](https://github.com/mathisarends/smelt)
in the development group. The executable policy is
[`smelt.yaml`](https://github.com/mathisarends/harnessed/blob/main/smelt.yaml).
It complements Ruff, ty, runtime tests and the architecture suite.

## Architecture policy

Every production module has an explicit architectural role. Strict coverage
rejects newly discovered code outside that policy. Examples and maintainer
scripts have reasoned exclusions: they exercise the SDK or repository tooling,
and remain covered by Ruff, ty, executable-example tests and distribution checks.

The kernel accepts only standard-library dependencies. Core additionally accepts
Pydantic. Feature, model and integration layers declare their optional imports.
Compaction may delegate model selection to the model integration; sandbox
integrations may publish the shell/filesystem backend contracts. SDK and testing
layers may reach their declared lower layers. Production never imports testing.

Import analysis includes `TYPE_CHECKING` edges and transitive paths. An allowed
facade must not provide a route around a lower layer's restrictions. The kernel
has a facade-only package contract: higher layers import its exported operations,
including DI binding plans and plugin-spec normalization, through
`harnessed.kernel`. Kernel implementations retain sibling imports.

The architecture tests additionally enforce relationships that the layer map
does not express: plugin independence and the individual modules permitted to
import provider SDKs, YAML, MCP clients and telemetry. The root SDK remains lazy
so a standalone kernel import never loads Pydantic or optional SDKs.

Cycle detection is an error at both **layer** and concrete **module** scope.
Smelt 0.4.0 follows resolved module edges instead of conflating entire child
packages. `type_checking_cycles: ignore` excludes annotation-only edges from cycle
graphs; `type_checking: include` still checks them against all layer and dependency
boundaries. Deferred runtime imports remain part of the module graph. Sibling
aggregation stays disabled because it does not prove a concrete module cycle.

## Source and test conventions

The requested checks are explicit in the config:

| Severity | Rules |
| --- | --- |
| Error | `import-cycle`, `public-definitions-first`, `bundled-instance-assignment`, `prefer-package-import`, `internal-static-helper`, `unnecessary-future-annotations`, `prefer-match-type-dispatch`, `implicit-optional-default` |
| Error | `package-only-dunder-all`, `private-symbol-import`, `private-class-constant`, `await-in-call-argument`, `deprecated-async-contextmanager-return`, `redundant-module-name`, `redundant-private-delegate`, `redundant-name-alias`, `prefer-deferred-annotations` |
| Error | `no-base-class-prefix`, `no-base-class-suffix`, `redundant-str-enum-value`, `str-enum-explicit-values`, `prefer-str-enum-discriminator`, `duplicate-literal-type`, `anonymous-tuple-type` |
| Error | `information-hiding` group: `internal-public-member`, `internal-public-module-symbol`, `internal-public-attribute`, `test-only-public-function`, `test-only-public-attribute` |
| Error | `private-test-access`, `string-patch-target`, `test-without-outcome-check`, `test-only-checks-possibly-empty-data`, `unbounded-test-wait`, `async-mock-without-await-check`, `nondeterministic-test-data`; at most three patches per test |
| Error | `missing-interface-docstring`, `missing-broad-except-reason`, `duplicate-function`, `duplicate-class-name`, `duplicate-narrowing-assert`, `prefer-named-duration`, `nested-conditional-expression`, `parallel-record-fields` |
| Error (policy) | `max_positional_parameters: 3` (`excess-positional-parameters`), `yaml_extension: yaml` (`yaml-file-extension`) |
| Hint | `independent-test-case-loop`, `deep-object-navigation`, `stateful-dataclass`, `mutable-field-in-frozen-dataclass`, `loop-invariant-collection` |

Hooks and CI use `--fail-on warning`, so metadata warnings such as unused
suppressions fail too. Optional defaults select a fallback only for `None`;
intentional empty-value behavior must be explicit. Future annotations remain
necessary for deferred names under the supported Python 3.12 minimum.

Public definitions precede implementation helpers. Module dunder assignments
follow those definitions. Only package initializers declare `__all__`; the
single-module public facades (`errors`, `hooks`, `services`, `sessions`,
`tracing`, the optional integrations, `plugins.mcp` and `plugins.subagents`)
have a reasoned config exception because their module path is the documented
import, and optional extras cannot be re-exported eagerly from a package. Class initializers and dunders precede public/private methods.
Ordering fixes use `safe-only`; reflection-sensitive sections require manual
review and behavioral verification. Production names such as `utils` and
`helpers` are forbidden in favor of responsibilities with concrete names.

`tests.layout: none` retains topic groups, root-level composition tests and
consumer typing contracts without requiring mirrored paths. Use public behavior
for tests wherever practical. Independent reference folds belong in test support;
they are useful oracles, not production API. Async coordination gets local
deadlines even when a test intentionally blocks work until cancellation.

Parameters after the third positional one are keyword-only, including injected
hook dependencies; dunder, `@overload` and `@override` signatures are exempt.
Fixed-shape tuples with three or more or repeated fields become `NamedTuple` or
dataclass records. The public `HookEntry` alias keeps its tuple form with an inline
reason because callers pass plain `(event, callback[, priority])` literals.
`StrEnum` values stay explicit strings so a member rename cannot silently change
a wire value. YAML files use `.yaml`; `.github/workflows/release.yml` keeps its
name because PyPI trusted publishing is registered for it.

The configuration deliberately leaves these optional policies unselected:

- Blanket private-module or module-docstring bans would conflict with intentional
  worker internals and useful module documentation.
- Relative-import enforcement can prefer one-dot imports of *child packages*;
  that does not express this repository's sibling-only preference. Ruff enforces
  the ban on parent-relative imports; cross-package imports remain absolute.
- Import-count/export-gap thresholds can suggest a design review, but frequency
  alone does not justify extending the SDK. Existing exports already drive the
  stricter `prefer-package-import` rule. A trial produced 162 hints that mostly
  contradict the canonical `core.messages` / `core.model` module imports.
- `caller-before-callee` would reorder 78 methods away from their lifecycle and
  protocol grouping; `public-definitions-first` already fixes the outer order.
- `prefer-callable-instance` reports protocol implementations such as approvers,
  summarizers and responders whose named method is the contract.
- `str-enum-auto-values` contradicts the explicit wire-value convention, and
  `secret-settings-field` / `redundant-dishka-provider` target frameworks this
  repository does not use.

## Reviewed exceptions

There is no debt baseline. The full check covers 159 modules with no
errors or warnings. Twelve individual inline findings are suppressed with reasons. One config exception permits private access only in
`tests/core/session/test_image_cache.py`, whose purpose is to measure byte and
entry limits, eviction and reuse. Public asset behavior remains in separate tests.

The inline exceptions cover the decorator exported by the lazy SDK, subscription
buffer ownership, deterministic lock contention, cgroup enforcement and private
worker transport tests. They preserve important invariants without publishing
implementation details as supported APIs. The remote-image regression exercises
attachments through `Agent.run` and checks the model request instead of calling the private image conversion method. New
exceptions need equally specific evidence.

Use stable codes for inline suppressions, on the reported statement or its
multiline header, for example:

```python
async with log._lock:  # smelt: ignore[SMT420] -- Force admission contention.
    ...
```

The config and CLI use readable rule names. Inline suppression syntax uses SMT
codes; a preceding standalone comment does not suppress the following statement.

## Working loop

```bash
uv run smelt context harnessed.core.runtime.session
uv run smelt check --changed --base origin/main --format json
uv run smelt check --show-hints --fail-on warning
uv run prek run --all-files
```

The local Git hook runs Smelt through prek and always checks the whole project
with the locked tool. CI invokes that same hook. Checking only changed filenames
could miss effects on importers. Regenerate the ignored editor schema after
installing/updating Smelt:

```bash
uv run smelt config schema > .smelt.schema.json
```

If a bundled Smelt agent skill is installed locally, refresh it with
`uv run smelt skill install --force`, preserving its custom destination and any
local edits first. This repository does not vendor a generated skill copy.

## Review after the 0.10.0 upgrade

The upgrade from 0.8.0 reviews all thirteen rules added in 0.9.0 and both rules
added in 0.10.0. Report schema 2, YAML version 1 and the default rule set are
unchanged. The exact development pin in `pyproject.toml` and `uv.lock` also
controls prek and CI; neither has an independent Smelt installation.

| Rule | Decision and evidence |
| --- | --- |
| SMT348 `missing-interface-docstring` | Error. Documented 55 placeholder methods with their behavioral, ownership or typing contract rather than repeating their signatures. |
| SMT349 `stateful-dataclass` | Hint. `InterruptedActivity` deliberately folds ordered events into mutable recovery state before producing repair records; it is not an immutable value. Review new stateful records against their owner. |
| SMT350 `missing-broad-except-reason` | Error. Added reasons at 20 exception boundaries: arbitrary application callbacks, model/plugin failures, durable results and cancellation-safe cleanup. The handlers keep their exception behavior. |
| SMT351 `missing-dataclass-option` | Not selected. Frozen configuration, mutable event-fold state, identity-based listeners and records using `cached_property` need different options. A package-wide `frozen`, `slots` or `kw_only` requirement would conflate those contracts; no uniform option policy is claimed. |
| SMT352 `duplicate-function` | Error. No initial findings; shared implementations must have one owner that both callers may import. |
| SMT353 `prefer-project-base-class` | Not selected. `PluginConfig` is the base for plugin configuration, not all Pydantic models. Wire replies, skill manifests and other payloads use their own validation policies; a package-wide `BaseModel` replacement would impose unrelated config semantics. |
| SMT354 `prefer-domain-exception` | Not selected. The standalone kernel owns its errors independently of core's `HarnessedError`. Argument/schema validation and filesystem operations also intentionally expose built-in exceptions. A blanket domain hierarchy would erase that distinction; allowing all those built-ins would add little enforcement. |
| SMT355 `duplicate-class-name` | Error. No initial findings; public class identities should be distinct within their owning package. |
| SMT356 `duplicate-narrowing-assert` | Error. No initial findings; repeated lifecycle narrowings should lead to a required constructor value or one checked property. |
| SMT357 `missing-duration-unit` | Not selected. The trial reports 67 declarations, largely `timeout`, `tool_timeout` and their protocol implementations. These follow the asyncio convention of seconds. Preserve that consistent API vocabulary and document seconds at the contract instead of imposing suffixes everywhere. Values in other units already use explicit names such as `delay_seconds` and `modified_ns`. |
| SMT358 `prefer-named-duration` | Error. No initial findings; named constants clarify nonzero wait durations without renaming timeout APIs. |
| SMT359 `nested-conditional-expression` | Error. Replaced 16 nested expressions with explicit branches or matches, preserving branch priority and lazy evaluation. |
| SMT360 `mutable-field-in-frozen-dataclass` | Hint. The three findings are `ToolArguments.names` and the cached `_EnvelopeSchema.properties` / `definitions` dictionaries. They are schema-builder data, where frozen binds the fields without promising deep immutability. Keep ownership review visible; changing annotations to `Mapping` alone would provide no isolation. |
| SMT361 `parallel-record-fields` | Error. Replaced `EnvelopeLayout`'s model/field/policy tuples with one sequence of named bindings. Construction, splitting and tool inspection now read the same record; mixed strict/drop policies and leading/trailing positions have behavioral regression coverage. |
| SMT362 `loop-invariant-collection` | Hint. No initial findings in the rule's conservative inference scope. Review any future hoist for guards, empty loops and exception timing. Pydantic/Annotated snapshots are outside this version's inference; a clean report is not a performance proof. |

The four initial ownership findings remain visible hints, with no new inline
suppression or debt. The existing deep-navigation hints retain the lifecycle
reasoning below. The upgrade changes no public SDK imports, timeout parameter
names, exception types or persisted formats.

## Review after the 0.8.0 upgrade

Smelt 0.5.0 to 0.8.0 added opt-in reviews; the adopted ones run as errors.
The upgrade made shared private helpers module-public (`validate_event`,
`activation_tool`, `prepare_input`, `MemoryAssets`, `SessionImages`,
`annotation_dependency`, `PluginRuntime`), removed `__all__` from internal modules,
privatized 17 internal data attributes, named awaited results and anonymous tuple
records, made excess positional parameters keyword-only and renamed
`core.prompt.system_prompt` to `core.prompt.system`. The checkpoint extension
discriminator uses `CheckpointRecordKind.EXTENSION` with an unchanged wire value.

Three exceptions remain inline: `PluginBase` keeps its suffix as a stable kernel
facade export that core `Plugin` extends, `Agent.plugin_validation` is a public
setting checked by the typing contract, and `HookEntry` keeps its tuple literal.

## Feedback from this adoption

### What helped

- Structured locations and replacement suggestions made scattered kernel/tool
  imports straightforward to consolidate. The kernel contract now prevents
  renewed coupling to its implementation modules.
- Source rules exposed internal methods with public names, a redundant static
  property-forwarding helper, unused wrappers and an obsolete second compaction
  input format. Removing those reduced API surface and duplicate paths.
- Public-first ordering made interfaces easier to find. Conservative autofix
  boundaries clearly separated automatic movement from sections requiring review.
- Test rules identified 50 unbounded wait findings. Local deadlines now turn
  missing release/cancellation into a bounded failure. Outcome checks also led to
  independently collected enum cases, explicit callback evidence and nonempty
  collection checks.
- Private-access findings prompted provider tests through the public model
  adapter, compaction tests through its manager, worker tests through the JSON
  response boundary, skill IO probes at the file boundary and a separate cache
  invariant test module. The reference turn fold moved into test support.
- Strict coverage and transitive paths make the intended architecture reviewable
  before edits and repeatable in CI, without adding Smelt to runtime dependencies.

### Review after the 0.4.0 upgrade

The upgrade enables exact module cycle detection and rejects implicit truthiness
defaults. Each optional-default finding is reviewed before rewriting; the model adapter
continues to reject a supplied zero context window before choosing its profile
default. Empty plugin names deliberately retain the class
name fallback through an explicit condition.

`deep-object-navigation` is an advisory design review, not a correctness proof.
The initial 19 findings cover session-owned log/approval/registry/image services,
shared kernel scope/runtime state, subprocess pipes and process-protocol buffers.
These paths preserve the owning object's lifecycle and current state. Creating
forwarding methods or cached aliases solely to silence them would not establish a
better boundary. Keep these hints visible for changes that alter those ownership
contracts, and review new findings on their merits.

The 0.12.0 release review covers 31 findings, including the child-session driver
and routed approvals. Parent log writes and flushes preserve the parent's durable
relationship records; child inbox steering uses atomic run admission. Runtime
control and approval services retain their owning session's lifecycle. These
paths remain within core runtime ownership and do not expose private kernel
registration state to higher layers. They remain advisory hints without new
suppressions.

Smelt 0.4.0 recognizes the guarded lazy SDK export table for member analysis.
General import-style enforcement still prefers
relative imports into child packages; Ruff retains the exact parent-relative ban
without changing cross-package ownership conventions.

These observations concern this repository and Smelt 0.4.0. They are feedback,
not new requirements imposed on downstream SDK users. Reports are local tooling
artifacts; no upstream issue or external message is created by these checks.
