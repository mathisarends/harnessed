# Smelt policy and adoption feedback

Harnessed pins [`smelt-cli` 0.3.0](https://github.com/mathisarends/smelt)
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

Cycle detection is an error at the **layer** scope. Smelt's sibling scope groups
entire child packages into graph nodes. Here, a package containing both contracts
and consumers can participate in a reported cycle even when its concrete runtime
modules do not form that cycle. `TYPE_CHECKING` back references also occur in the
kernel and typed runtime components. Do not enable sibling-scope failures until
those findings can distinguish the relevant concrete module edges. Layer and
transitive dependency checks still apply to those modules and annotations.

## Source and test conventions

The requested checks are explicit in the config:

| Severity | Rules |
| --- | --- |
| Error | `import-cycle`, `public-definitions-first`, `bundled-instance-assignment`, `redundant-str-enum-value`, `prefer-package-import`, `internal-public-member`, `internal-static-helper` |
| Error | `private-test-access`, `string-patch-target`, `test-only-public-function`; at most three patches per test |
| Warning | `internal-public-module-symbol`, `unnecessary-future-annotations`, `prefer-match-type-dispatch`, `test-only-public-attribute`, `test-without-outcome-check`, `test-only-checks-possibly-empty-data`, `unbounded-test-wait`, `async-mock-without-await-check` |
| Hint | `independent-test-case-loop` |

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

There is no debt baseline. The full adoption check covers 116 governed modules
with no errors, warnings or hints. Nine individual inline findings are suppressed
with reasons. One config exception permits private access only in
`tests/core/session/test_image_cache.py`, whose purpose is to measure byte and
entry limits, eviction and reuse. Public asset behavior remains in separate tests.

The inline exceptions cover the decorator exported by the lazy SDK, subscription
buffer ownership, deterministic lock contention, cgroup enforcement and private
worker transport tests. They preserve important invariants without publishing
implementation details as supported APIs. The cold-cache asset regression also
documents why it clears the private cache; Smelt does not currently resolve that
chained attribute access. New exceptions need equally specific evidence.

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
uv run smelt check --show-hints
uv run pre-commit run --all-files
```

The local pre-commit hook always checks the whole project with the locked tool.
CI invokes that same hook. Checking only changed filenames could miss effects on
importers. Regenerate the ignored editor schema after installing/updating Smelt:

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

### What would help more

1. Offer concrete-module runtime cycle detection alongside aggregated sibling
   cycles, with separately configurable treatment of annotation-only cycles.
   Preserve each edge and its import phase in the diagnostic witness.
2. Recognize static lazy-export tables paired with `__getattr__` and the SDK
   type stub. `instructions` is a documented public export, but currently needs
   a reasoned exception to internal-member analysis.
3. Diagnose unknown suppression identifiers. During adoption, readable rule names
   in `ignore[...]` comments were silently unrecognized, whereas unused valid
   SMT-code suppressions were diagnosed. Supporting names consistently would
   also remove this syntax difference between YAML, CLI and comments.
4. Explain why a directly awaited shared assertion helper supplies no recognized
   evidence. The live-sandbox helper performs assertions throughout its lifecycle;
   its callers initially received missing-outcome warnings. Caller-side exported
   file checks are useful, but the diagnostic should expose the analysis limit.
5. Allow an import convention that prefers relative imports only within the
   exact same package, while retaining absolute imports into child packages.
6. Distinguish reflection on another object's namespace from inspection of the
   definitions being ordered. Current `vars`, `globals` and `nonlocal` barriers
   appropriately protect semantics but leave several manually reviewable cases.

These observations concern this repository and Smelt 0.3.0. They are feedback,
not new requirements imposed on downstream SDK users. Reports are local tooling
artifacts; no upstream issue or external message is created by this adoption.
