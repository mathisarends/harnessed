# Smelt policy and adoption feedback

Harnessed pins [`smelt-cli` 0.4.0](https://github.com/mathisarends/smelt)
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
| Error | `import-cycle`, `public-definitions-first`, `bundled-instance-assignment`, `redundant-str-enum-value`, `prefer-package-import`, `internal-public-member`, `internal-static-helper`, `internal-public-module-symbol` |
| Error | `unnecessary-future-annotations`, `prefer-match-type-dispatch`, `implicit-optional-default`, `no-base-class-prefix` |
| Error | `private-test-access`, `string-patch-target`, `test-only-public-function`, `test-only-public-attribute`; at most three patches per test |
| Error | `test-without-outcome-check`, `test-only-checks-possibly-empty-data`, `unbounded-test-wait`, `async-mock-without-await-check` |
| Hint | `independent-test-case-loop`, `deep-object-navigation` |

Hooks and CI use `--fail-on warning`, so metadata warnings such as unused
suppressions fail too. Optional defaults select a fallback only for `None`;
intentional empty-value behavior must be explicit. Future annotations remain
necessary for deferred names under the supported Python 3.12 minimum.

Public definitions precede implementation helpers. Module dunder assignments
follow those definitions, preserving the repository's `__all__`-at-the-bottom
convention. Class initializers and dunders precede public/private methods.
Ordering fixes use `safe-only`; reflection-sensitive sections require manual
review and behavioral verification. Production names such as `utils` and
`helpers` are forbidden in favor of responsibilities with concrete names.

`tests.layout: none` retains topic groups, root-level composition tests and
consumer typing contracts without requiring mirrored paths. Use public behavior
for tests wherever practical. Independent reference folds belong in test support;
they are useful oracles, not production API. Async coordination gets local
deadlines even when a test intentionally blocks work until cancellation.

The configuration deliberately leaves these optional policies unselected:

- Blanket private-module or module-docstring bans would conflict with intentional
  worker internals and useful module documentation.
- Relative-import enforcement can prefer one-dot imports of *child packages*;
  that does not express this repository's sibling-only preference. Ruff enforces
  the ban on parent-relative imports; cross-package imports remain absolute.
- Import-count/export-gap thresholds can suggest a design review, but frequency
  alone does not justify extending the SDK. Existing exports already drive the
  stricter `prefer-package-import` rule.

## Reviewed exceptions

There is no debt baseline. The full check covers 118 governed modules with no
errors or warnings. Nine individual inline findings are suppressed with reasons. One config exception permits private access only in
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
