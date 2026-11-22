# Changelog

User-visible changes, newest first. Agents should read the latest release's
migration notes before interpreting fewer findings as an improvement. For the
current configuration and complete rule contracts, see the [README](README.md),
[rule catalog](docs/rules/README.md) and [activation guide](docs/rule-selection.md).

## [Unreleased]

## [0.16.1] - 2026-11-22

See [upgrading from 0.16](docs/upgrading.md#016-to-0161) for hook pins and
cache behavior.

- Cold checks share syntax tree walks across rules. The check worker now
  releases cached walks when it no longer references the corresponding trees.
- Editable installations reuse analysis facts after changes to the CLI or
  rules. An edit to one rule refreshes the decisions that depend on that
  rule's implementation.
- Links in the PyPI package description now point to the matching release's
  documentation and changelog.
- `check`, `check --staged` and `check --changed` reuse each other's cached
  facts. Git-backed checks identified a file by its object ID and listed every
  tracked Python file for import resolution, so the first staged or changed
  check after a full check analyzed the unchanged project again. All modes now
  identify a file by its decoded text and the same package inventory.
- `check --changed` reads its base revision from Git objects instead of
  exporting the whole repository to a temporary directory, so the base reuses
  the facts of unchanged files as well.
- `.smelt_cache/` takes far less disk space. Cached facts are stored compressed,
  about a quarter of their former size, and at most seven caches of other Smelt
  versions are kept besides the current one. Previously an editable checkout
  or several worktrees on different Smelt versions could accumulate gigabytes
  within the seven-day window. Existing caches remain readable. See
  [cache behavior](https://github.com/mathisarends/smelt/blob/main/docs/agent-workflow.md#cache-behavior).

## [0.16.0] - 2026-11-22

See [upgrading from 0.15](docs/upgrading.md#015-to-016) for hook pins and
previewing the new fixes.

- The README presents Smelt as a programmatically enforced style guide, and the
  documentation starts with a six-level learning path, from a
  [first check](https://github.com/mathisarends/smelt/blob/main/docs/01-first-check.md) to
  [scaling the policy](https://github.com/mathisarends/smelt/blob/main/docs/06-scaling-the-policy.md) across uv workspaces. The
  existing guides remain as reference material behind the
  [documentation index](https://github.com/mathisarends/smelt/blob/main/docs/README.md).
- More rules carry machine-readable fixes for `check --fix` and `check --diff`.
  `--fix` applies the safe edits from [MET001](docs/rules/MET001.md) for unused
  suppressions or identifiers and retired rule names. It also applies safe edits
  from [TYP004](docs/rules/TYP004.md), [IMP003](docs/rules/IMP003.md),
  [LAY006](docs/rules/LAY006.md), [LAY011](docs/rules/LAY011.md),
  [MOD004](docs/rules/MOD004.md) and the `as` alias of [VIS008](docs/rules/VIS008.md).
  Add `--unsafe-fixes` to apply [TYP006](docs/rules/TYP006.md) when it has to add the future
  import, [TYP008](docs/rules/TYP008.md), [IMP004](docs/rules/IMP004.md),
  [MOD006](docs/rules/MOD006.md), [LAY008](docs/rules/LAY008.md),
  [IDM001](docs/rules/IDM001.md) and VIS008 entries added to `__all__`.
  IDM001 can change when values are evaluated and attributes are written.
  The JSON report's `fix.applicability` field identifies which edits require
  opt-in.
- Fixes with the `display` applicability are suggestions: the concise and text
  formats mark them `[suggestion]` and count them in the summary, and
  `check --diff --suggestions` previews them; `--fix` never applies them.
  [CPX003](docs/rules/CPX003.md) suggests the `*` that makes the excess
  parameters keyword-only, because every call site has to change too, and
  MET001 suggests removing unknown suppression identifiers.

## [0.15.0] - 2026-11-22

See [upgrading from 0.14](docs/upgrading.md#014-to-015) for hook pins,
the JSON fix report, third-party import suggestions and facade suppressions.

- `smelt context <target> --guide` prints one instruction per active rule for
  writing the target, rendered from `smelt.yml`: allowed layer imports, the
  package's positional-parameter limit, required dataclass options, the
  re-export and future-import conventions and so on. Source targets also list
  the rules for their mirrored test file; test paths are accepted as targets.
  Errors, warnings and hints are listed separately. The guide skips analysis,
  and `--format json` returns each rule's code, severity and instruction.
  `smelt explain` and the rule pages show the rule's instruction as `Guide`.

- Findings can carry a machine-readable fix: text edits with an `original` and a
  `replacement` plus an applicability (`safe`, `unsafe` or `display`). JSON
  findings include it under `fix`, the concise and text formats mark fixable
  findings with `[fix]` or `[unsafe fix]` and count them in the summary, and
  `smelt check --diff` previews the fixes as a unified diff without writing.
  `smelt rules --format json` and the rule pages state whether a rule's findings
  carry a fix. [LAY004](docs/rules/LAY004.md) and [TYP003](docs/rules/TYP003.md)
  now use this model and one shared `--fix` engine.
  **Migration:** with `--fix`, JSON `fixes` now holds `applied`, `files` and
  `skipped` (each with `path`, `code`, `line` and `reason`) instead of
  `fixes.ordering` and `fixes.none_defaults`. A LAY004 finding whose section
  cannot be rewritten safely reports the reason in `fix_reason` and has no fix.

- [IMP004](docs/rules/IMP004.md) can suggest the public package import of an
  installed dependency, such as `from fastapi import APIRouter` instead of
  `from fastapi.routing import APIRouter`. List the top-level import names in
  `style.third_party_reexports` (or a style override) and enable
  `imports/prefer-package-import`. Smelt reads the packages from its own
  interpreter without importing them; run it in the project environment.

- [CPX008](docs/rules/CPX008.md) can flag a direct method call on an inline
  constructor, so the instance can be named first. Enable
  `complexity/inline-constructor-receiver` in `rules`. Imported class names are
  recognized by convention.

- `architecture/non-public-module-import` (ARC007) and
  `imports/excess-package-imports` (IMP001) treat `from app.orders import capture`
  as an import of the `app.orders` facade when the facade binds `capture` to an
  object other than its same-named submodule, as `from .capture import capture`
  does. Plain `import app.orders.capture` and names the facade does not bind
  still count as the submodule. Suppressions written for this false positive are
  now reported by `meta/unused-suppression`.

- A check in a linked Git worktree reads and writes the fact cache of the same
  project in the main checkout, so new agent worktrees skip the cold analysis.

- Checks share syntax walks and facade usage across rules. After an edit, they
  reuse unchanged modules' facade bindings, imports, private attribute facts,
  enum bindings and duplicate-function fingerprints.

- Changed and staged checks refresh IMP004 findings and their historical
  comparison when a listed dependency changes in the Python environment.

- [TST012](docs/rules/TST012.md) can report a source module with behaviour that
  has no test at its mirrored path under `tests.layout: mirror`. Enable
  `tests/missing-mirror-test` in `rules`.

- Package contracts accept `test_access: mirrored`. A test whose `tests.mirror`
  path mirrors a module inside the package counts as inside it for
  [ARC007](docs/rules/ARC007.md) and [IMP001](docs/rules/IMP001.md), so unit
  tests may import the internals of the package they mirror without
  suppressions. The default `public` keeps tests outside callers.

- `style.navigation` declares namespace evidence for
  [CPX001](docs/rules/CPX001.md) about code Smelt cannot analyze:
  `namespace_types` for SDK clients whose attributes group domain APIs and
  `instance_factories` for typed descriptors or dependency markers such as
  `State(Counts)`. Package overrides can set or lift it.

## [0.14.0] - 2026-11-21

### Upgrade from 0.13

1. Run `smelt config migrate` with Smelt 0.14. Supply any required policy it
   reports, or remove the rule entry if it was intentionally inactive. Review
   the diff and apply it with `smelt config migrate --write`.
2. Update inline `# smelt: ignore[?]` comments by hand. For example, replace
   `SMT420` with `tests/private-access` or `TST002` and keep the original reason.
   The [upgrade guide](docs/upgrading.md#use-the-family-rule-codes) lists the
   replacement codes.
3. Update your [hook and CI pins](docs/integrations.md) and run
   `smelt check --fail-on warning`.

### Changes

- Staged checks reuse production-only rule results after test edits. Editing a
  rule in a source installation preserves parsed analysis facts while refreshing
  rule decisions and the worker.

- **Breaking:** Rule codes start with their family instead of `SMT`: `ARC`
  architecture, `IMP` imports, `LAY` layout, `VIS` visibility, `MOD` modeling,
  `TYP` typing, `CPX` complexity, `IDM` idioms, `FWK` frameworks, `TST` tests
  and `MET` meta, numbered within the family (`SMT101` is now `ARC001`). Findings,
  JSON, SARIF and rule pages (`docs/rules/ARC001.md`) use the new codes. Old
  `SMTnnn` codes and prefixes are rejected in `rules`, `ignore`, `--select`,
  `--ignore` and `smelt explain`, and reported as unknown in inline
  suppressions; each error names the new code. `smelt config migrate` replaces
  old codes in `rules` with rule names and in `ignore` with the new codes.
  Existing debt baselines keep matching. See the
  [upgrade guide](docs/upgrading.md#use-the-family-rule-codes).

- `meta/uncovered-package-facade` (MET005, hint, on by default) reports a package
  initializer on full checks when an explicitly enabled
  `imports/prefer-package-import` has nothing to check there: more than `max`
  (default 2) outside production modules import public names from its
  implementation modules that no facade re-exports. A facade with its own active
  `architecture/missing-facade-export` threshold is skipped. Set `max` in the
  root entry, e.g. `meta/uncovered-package-facade: {severity: hint, max: 4}`.
  Closes the remaining part of #169.

- `smelt rules` shows a `status` column for the current project instead of
  the activation mode: `on` (with the effective severity), `scoped` (enabled
  only in style overrides or members), `off` or `needs policy`. Without a
  `smelt.yml` it reports the catalog status. JSON entries add `status` and
  `severity`.
- `smelt explain` opens with fixed `Enable`, `Options`, `Requires` and, inside
  a project, `Status` lines. JSON adds `enable`, `options`,
  `requires_policy` and `status`.
- **Breaking:** `rules` is the only switch that enables a rule. Declared
  policies such as `architecture.layers`, `tests.layout: mirror` or
  `style.imports` no longer enable their rules. A policy whose rule no `rules`
  entry names is a configuration warning, and enabling a rule whose required
  policy is missing is a configuration error. `smelt init` writes the entries
  for the inferred policy; `smelt config migrate` adds them to existing configs.
  `smelt rules` shows the modes `default` and `explicit`. See the
  [upgrade guide](docs/upgrading.md#013-to-014).
- **Breaking:** The configuration format is `version: 2`, and `version: 1` is
  no longer read; the error points to `smelt config migrate --write`, which
  converts the root, its bases and workspace members together.
- `smelt config migrate` accepts empty policy bases and preserves root rule
  activation with local exceptions. Moving limits preserves severity caps set
  by families or `ALL`, including in `extends` bases, and keeps inherited
  severities for package limits.
- **Breaking:** Limits moved into their rule entries as
  `rules: {name: {severity, max}}`. `style.max_module_imports`,
  `max_positional_parameters`, `max_comprehension_loops`,
  `max_constructor_decisions`, `max_private_definitions` and
  `tests.max_patches_per_test` were removed. An entry with only `max` in a style
  override or member refines the limit and keeps the inherited severity.
  Like other policy options, `style.module_import_exemptions` without an
  IMP002 entry is a configuration warning.
- **Breaking:** `project.coverage` was removed. Enable strict coverage with
  `rules: {architecture/uncovered-source: error}` in the root config, or for one
  run with `--strict-coverage`. JSON reports keep `coverage.mode`.
- `smelt init --maintainability` lists every explicitly selectable rule. Rules
  outside the starter selection appear commented out with their catalog
  severity, and the starter contains a single `rules` block.
- `--format concise` omits the usage-limits note and reports policy coverage
  only when it is incomplete.
- CLI help uses "findings" and "families" consistently, and the hook examples
  use `.pre-commit-config.yaml`, which both prek and pre-commit read.

- Native checks keep waiting for a busy analyzer after five seconds. An idle or
  disconnected worker still falls back, with a five-minute limit on active requests.

- `smelt context` reuses the persistent analysis cache populated by `smelt check`.

- Enable [TYP008](docs/rules/TYP008.md) with `typing/prefer-inline-type-parameters: hint`
  to review explicit `Generic[...]` bases for PEP 695 class syntax on Python 3.12+.
- **Breaking:** `architecture.modules` was removed. `smelt config migrate`
  moves assignments to `architecture.layers.<layer>.modules` across the
  selected project, its bases and workspace members. See the
  [upgrade guide](docs/upgrading.md#move-layer-assignments-into-layers).
- `.smelt_cache/` no longer grows without bound. Facts unused for 14 days are
  pruned once a day, and caches of other Smelt versions unused for 7 days are
  removed. Existing caches are upgraded in place. See
  [cache behavior](https://github.com/mathisarends/smelt/blob/main/docs/agent-workflow.md#cache-behavior).
- Configuration errors arrive in one report. Schema errors such as retired
  keys now list unknown or retired rule names from `rules` and `ignore`
  alongside them. A valid layer declaration also reports unknown layer references
  alongside unrelated schema errors. Every unknown `ignore[].rule` is reported at once.
- Inline suppressions accept rule names, such as
  `# smelt: ignore[architecture/layer-boundary] -- reason`, alongside rule codes
  and prefixes. [MET001](docs/rules/MET001.md) reports unknown or unused
  identifiers as written and names the replacement for a retired or misspelled
  rule name.
- Migration errors for retired keys and rules link the installed release's upgrade
  guide once per text report. Structured JSON issues retain their individual links.
  The removed `information-hiding` selector lists the five rules that replace it.
- `smelt context` shows where a rule's setting comes from as
  `(smelt.yml: rules.<name>); reason: ...` instead of a separator that read
  like a question.
- Text rule explanations render boolean details as yes or no and omit
  constant `automatic_*` flags.
- Declare explicit package prefixes or module names in each layer's `modules`
  list. Smelt warns about redundant descendants, `smelt init` writes grouped
  assignments, and `smelt context` reports which assignment a planned module
  inherits. ARC011 respects explicit assignments when checking layout. See the
  [library layout guide](docs/architecture.md#libraries-and-mixed-package-layouts).
- Enable [LAY011](docs/rules/LAY011.md) with `layout/misplaced-module-logger: warning`
  to keep module loggers immediately after imports and before constants.
- Set `style.reexports` to `all` or `alias` to have [VIS008](docs/rules/VIS008.md)
  check explicit re-export spelling in package initializers. Package overrides
  can select another spelling or allow an intentional runtime export list.
- Enable [LAY010](docs/rules/LAY010.md) with `layout/reexport-only-module: hint`
  to review ordinary modules made entirely of explicit re-exports.
- The `smelt.engine.workspace` import path was removed. Import `WorkspaceMember`,
  `import_root` and `workspace_members` from `smelt.analysis.workspace`.
- Full checks warn with [MET004](docs/rules/MET004.md) when an explicit setting
  for ARC007, IMP001, ARC008 or IMP003 lacks its required policy in that scope
  but the policy exists elsewhere in the project. An enabled rule with no
  required policy anywhere is a configuration error.
- Set `style.public_attribute_boundary: module` to let [VIS004](docs/rules/VIS004.md)
  suggest private names for fields used by same-module helpers and classes.
  The default is `class`. [VIS010](docs/rules/VIS010.md) uses the same boundary
  when enabled and continues to cover test-only reads.

## [0.13.0] - 2026-11-18

- Configuration now distinguishes shared `style` and `rules`, package APIs under
  `architecture.packages`, and independent member architectures. See
  [configuration and inheritance](docs/configuration.md).
- The catalog uses individual `family/finding` names. The
  [0.13 upgrade checklist](docs/upgrading.md#012-to-013) covers renamed and
  merged rules, moved YAML keys, workspace ownership and report consumers.
- `smelt context` explains the effective policy and exception reasons for a
  target. Workspace initialization drafts one shared root config and local
  member configs.

- SMT316 and SMT320 preserve public runtime entry points in declared composition roots.

### Breaking configuration layout

- `style` sets project-wide conventions and `style.overrides` sets package style
  and rule choices. `architecture.packages` defines public and required modules
  with facade thresholds. Canonical exports now belong to `architecture`.
- `architecture.import_graph` configures dependency analysis, while `style.imports`
  configures Python import syntax. Threshold and exemption keys have clearer
  names. See the [0.13 migration guide](docs/upgrading.md#012-to-013).

### Context

- `smelt context` shows effective style and rule settings, their declaring files,
  and exception reasons. It lists package APIs separately in the text view.
- Source-convention exceptions can use `{value: ..., reason: ...}`. The older
  `reasons` map is still accepted. `smelt config show --sources` lists member
  policy and explicit origins alongside the resolved root config.

- Workspace context names the owner, governing configuration and rules layers.
  Scoped workspace checks label whole-workspace totals and policy coverage.

- `smelt context` now identifies the feature and layer of a planned path inside
  a new feature, including in workspace members. JSON marks these targets
  with `feature_planned`.

### Workspace policy and initialization

- Workspace init defers incomplete layer drafts and configures mirror tests
  when every existing test matches. If pytest module names collide, init
  lists the clashing files and leaves mirroring disabled.

- **Member weakening requires a reason.** Lowering inherited rule severity or
  setting `off` in member rules or style overrides now requires
  `{severity: ..., reason: "..."}`. The guard resolves selectors, catalog
  defaults and root policy activation and overrides before comparing. A winning
  individual entry needs its own reason. Strict coverage still cannot be
  lowered. Root settings also accept the object form; short values remain valid.
- `smelt init` in a uv workspace writes shared root policy and member config
  drafts. Local layers and features govern only their owning project or member.
  Move member package APIs and style overrides out of the root and into the
  owning member config. Init pre-fills its commented member dependency example
  with observed cross-member imports. New members inherit root style and
  automatically discovered roots. `init --member PATH` drafts only one declared
  member under an existing root. Init checks every target before writing and
  refuses existing targets without `--force`; full workspace force overwrites
  all config targets. See [workspace init](docs/configuration.md#workspace-initialization).
- Member-relative `debt` files accept only that member's findings, with stored
  paths remaining workspace-relative. Root debt is the fallback for other files.
  The debt CLI records per-member baselines at the root, or only the selected
  member. `--prune` does not create new member scopes; multi-file JSON inspection
  uses a `files` map. See [workspace debt](docs/adoption.md#workspace-debt).
- Member weakening errors identify the YAML selector as written and show
  the declaring configuration file relative to the project root.
- Member-directory invocations also select the member when it has no local
  config, including staged checks. The bundled skill documents the canonical
  root-style/member-architecture/root-boundary workflow. A portable uv fixture
  covers inheritance, different architectures, cross-member imports and debt.
- Member source conventions that change a root setting now require a nonempty
  reason under `reasons`, in member `style` or the relevant style override.
  Add reasons to existing overrides when upgrading.

### Workspace member boundaries

- Workspace init includes a commented member allowlist draft with observed
  cross-member imports for review.

- SMT112 recommends removing or moving imports that the workspace member
  allowlist forbids. Allowed imports still get the dependency declaration hint.

- Root `architecture.members` maps workspace-relative source globs to
  `may_depend_on` target globs and enables SMT111 `architecture/member-boundary`.
  Unmatched members are unrestricted; overlapping source entries combine their
  allowances. A key or allowance glob that matches no analyzed member (or `.`)
  is a configuration error. Configure member boundaries in the root policy of a
  uv workspace with `project.workspace: uv`. See
  [member boundaries](docs/configuration.md#boundaries-between-members).
- A uv workspace enables SMT112 `architecture/undeclared-member-dependency` for
  direct production imports of another member without a static project or
  optional dependency. It resolves distribution names and inherited/member uv
  sources, without evaluating environment markers. Dynamic/missing metadata and
  test imports are outside its inference limits. Both rules honor root
  `TYPE_CHECKING` policy and check direct imports only.
- JSON findings add `member` (workspace-relative directory, `.` for root files,
  `null` otherwise). Text/concise `--statistics --by member` groups all findings,
  including hints, by member. `coverage.member_boundaries` exposes the root
  allowlists with `--show-coverage`. Report schema 2 and finding/debt identities
  remain unchanged.

**Breaking: rule taxonomy.** Every rule name now has the form
`family/finding`, several names were corrected to the documented qualifiers, and
seven pairs of rules that described one concept were merged. YAML
version 1, report schema 2, exit codes and the default rule set remain unchanged,
and every unmerged SMT code keeps its identity. Configuration that uses a 0.12
rule name, or a retired code, fails with an error naming the replacement. See
the [upgrade checklist](docs/upgrading.md#012-to-013).

### Families

- Rules are grouped into eleven families: `architecture`, `imports`, `layout`,
  `visibility`, `modeling`, `typing`, `complexity`, `idioms`, `frameworks`,
  `tests` and `meta`. The family is the first segment of the name.
- A family name selects its rules for CLI exploration and ignores. In YAML it
  can disable or cap active rules. An individual setting wins in the same map.
  `smelt explain <family>` lists a family's rules.
- The report `category` field and the `category` of `smelt rules --format json`
  now carry the family (`architecture`, `layout`, ...) instead of `dependencies`,
  `structure` and `tests`. The success line of `smelt check` lists the families
  that ran, and the `smelt rules` text output is grouped by family.
- Rule codes are unchanged. The SMT1xx/3xx/4xx/9xx ranges are historical and no
  longer imply a category.

### Merged rules

The surviving rule keeps the lower code. The retired codes are no longer
accepted in `rules`, `--select`, `--ignore` or inline suppressions:

| Retired | Now | Rule | How the two behaviors are combined |
| --- | --- | --- | --- |
| SMT325 | SMT309 | `layout/module-docstring-convention` | One rule follows `module_docstrings: require` or `forbid`. Activation is unchanged: either policy enables it. |
| SMT326 | SMT323 | `modeling/forbidden-base-class-affix` | Reports both a `Base` prefix and a `Base` suffix. The payload names the `position`. Projects that enabled only one of the two now see both. |
| SMT332 | SMT331 | `layout/file-name-convention` | `style.file_naming.yaml_extension` and `compose_basename` produce one finding per file. That finding lists every correction, for example `filename must use .yml and compose: ...`. The payload holds one key per violated option plus the corrected `path`. |
| SMT342 | SMT341 | `modeling/str-enum-value-convention` | New option `style.str_enum_values: explicit` (default) or `auto`. The single rule enforces whichever is chosen. The payload adds `str_enum_values`. |
| SMT358 | SMT357 | `modeling/implicit-duration-unit` | Reports duration names without a unit and inline duration literals. The payload `kind` is `constant`, `field`, `parameter` or the new `literal`. Projects that enabled only one form now see both. |
| SMT365 | SMT339 | `visibility/foreign-private-access` | Reports imports and reads of another module's private symbols, plus accesses to private attributes owned by another module. The payload `kind` is `symbol` or `attribute`. `style.private_attribute_boundary` still applies only to attributes. Projects that enabled only one form now see both. |
| SMT366 | SMT340 | `complexity/inline-call-argument` | Reports awaited results, eager comprehensions and builtin reductions passed directly as call arguments. New option `style.call_argument_kinds` (default `[await, comprehension, aggregate]`) narrows the review. The payload `kind` is `await`, `comprehension` or `aggregate`. |

SMT310 `typing/forbidden-future-annotations` and SMT311
`typing/redundant-future-annotations` deliberately stay separate. SMT310 is a
certain policy error. SMT311 is a heuristic hint about boilerplate that might
still be needed. Merging them would force one severity onto two different levels
of certainty.

SMT340 reports generator expressions passed to an unshadowed builtin `all`,
`any`, `max`, `min`, `sorted` or `sum` as `kind: aggregate` when the reduction
is itself a direct call argument, for example `finished=sum(s.done for s in sessions)`. The
whole reduction is named, so the generator stays lazy. The payload keeps
`aggregate`, which is `null` for comprehensions.

### Renamed rules

These names were corrected to the qualifiers in CONTRIBUTING.md:

- `no-`, `unnecessary-` and similar prefixes became `forbidden-` and `redundant-`.
- `excess-` is used for limits.
- `misplaced-` is used for code in the wrong place.
- `-convention` marks rules that follow a policy option.
- Finding names no longer repeat the family, so `forbidden-test-mock` became
  `tests/forbidden-mock`.

The full mapping:

| Code | 0.12 name | 0.13 name | Change |
| --- | --- | --- | --- |
| SMT101 | `layer-boundary` | `architecture/layer-boundary` | family prefix |
| SMT102 | `cross-feature-import` | `architecture/cross-feature-import` | family prefix |
| SMT103 | `third-party-denied` | `architecture/forbidden-third-party-import` | renamed |
| SMT104 | `import-cycle` | `architecture/import-cycle` | family prefix |
| SMT105 | `shared-imports-feature` | `architecture/shared-imports-feature` | family prefix |
| SMT106 | `composition-root-leak` | `architecture/composition-root-leak` | family prefix |
| SMT107 | `private-module-import` | `architecture/non-public-module-import` | renamed |
| SMT108 | `package-import-fanout` | `imports/excess-package-imports` | family prefix |
| SMT109 | `facade-export-gap` | `architecture/missing-facade-export` | renamed |
| SMT110 | `module-import-fanout` | `imports/excess-module-imports` | renamed |
| SMT301 | `unknown-layer` | `architecture/unknown-layer` | family prefix |
| SMT302 | `forbidden-package-name` | `layout/forbidden-module-name` | renamed |
| SMT305 | `unclassified-module` | `architecture/unclassified-module` | family prefix |
| SMT306 | `incomplete-coverage` | `architecture/uncovered-source` | renamed |
| SMT307 | `missing-required-module` | `architecture/missing-required-module` | family prefix |
| SMT308 | `import-style` | `imports/forbidden-import-style` | renamed |
| SMT309 | `forbidden-module-docstring` | `layout/module-docstring-convention` | renamed |
| SMT310 | `forbidden-future-annotations` | `typing/forbidden-future-annotations` | family prefix |
| SMT311 | `unnecessary-future-annotations` | `typing/redundant-future-annotations` | renamed |
| SMT312 | `no-private-modules` | `layout/forbidden-private-module` | renamed |
| SMT313 | `public-definitions-first` | `layout/definition-order` | renamed |
| SMT314 | `bundled-instance-assignment` | `idioms/bundled-instance-assignment` | family prefix |
| SMT315 | `prefer-match-type-dispatch` | `idioms/prefer-match-type-dispatch` | family prefix |
| SMT316 | `internal-public-member` | `visibility/internal-public-member` | family prefix |
| SMT317 | `internal-static-helper` | `layout/misplaced-static-helper` | renamed |
| SMT318 | `redundant-str-enum-value` | `modeling/redundant-str-enum-value` | family prefix |
| SMT319 | `prefer-package-import` | `imports/prefer-package-import` | family prefix |
| SMT320 | `internal-public-module-symbol` | `visibility/internal-public-module-symbol` | family prefix |
| SMT321 | `implicit-optional-default` | `typing/implicit-optional-default` | family prefix |
| SMT322 | `deep-object-navigation` | `complexity/excess-attribute-depth` | family prefix |
| SMT323 | `no-base-class-prefix` | `modeling/forbidden-base-class-affix` | renamed |
| SMT324 | `prefer-str-enum-discriminator` | `modeling/prefer-str-enum-discriminator` | family prefix |
| SMT325 → SMT309 | `missing-module-docstring` | `layout/module-docstring-convention` | merged |
| SMT326 → SMT323 | `no-base-class-suffix` | `modeling/forbidden-base-class-affix` | merged |
| SMT327 | `secret-settings-field` | `frameworks/prefer-secret-type` | renamed |
| SMT328 | `redundant-dishka-provider` | `frameworks/redundant-dishka-provider` | family prefix |
| SMT329 | `caller-before-callee` | `layout/callee-before-caller` | renamed |
| SMT330 | `package-only-dunder-all` | `visibility/misplaced-dunder-all` | renamed |
| SMT331 | `yaml-file-extension` | `layout/file-name-convention` | renamed |
| SMT332 → SMT331 | `compose-file-name` | `layout/file-name-convention` | merged |
| SMT333 | `prefer-callable-instance` | `idioms/prefer-callable-instance` | family prefix |
| SMT334 | `private-class-constant` | `layout/misplaced-private-constant` | renamed |
| SMT335 | `internal-public-attribute` | `visibility/internal-public-attribute` | family prefix |
| SMT336 | `redundant-module-name` | `layout/redundant-module-name` | family prefix |
| SMT337 | `deprecated-async-contextmanager-return` | `typing/deprecated-async-contextmanager-return` | family prefix |
| SMT338 | `duplicate-literal-type` | `typing/duplicate-literal-type` | family prefix |
| SMT339 | `private-symbol-import` | `visibility/foreign-private-access` | renamed |
| SMT340 | `await-in-call-argument` | `complexity/inline-call-argument` | renamed |
| SMT341 | `str-enum-auto-values` | `modeling/str-enum-value-convention` | renamed |
| SMT342 → SMT341 | `str-enum-explicit-values` | `modeling/str-enum-value-convention` | merged |
| SMT343 | `redundant-private-delegate` | `visibility/redundant-private-delegate` | family prefix |
| SMT344 | `redundant-name-alias` | `idioms/redundant-name-alias` | family prefix |
| SMT345 | `excess-positional-parameters` | `complexity/excess-positional-parameters` | family prefix |
| SMT346 | `prefer-deferred-annotations` | `typing/prefer-deferred-annotations` | family prefix |
| SMT347 | `anonymous-tuple-type` | `typing/anonymous-tuple-type` | family prefix |
| SMT348 | `missing-interface-docstring` | `idioms/missing-interface-docstring` | family prefix |
| SMT349 | `stateful-dataclass` | `modeling/dataclass-field-mutation` | family prefix |
| SMT350 | `missing-broad-except-reason` | `idioms/missing-broad-except-reason` | family prefix |
| SMT351 | `missing-dataclass-option` | `modeling/missing-dataclass-option` | family prefix |
| SMT352 | `duplicate-function` | `complexity/duplicate-function` | family prefix |
| SMT353 | `prefer-project-base-class` | `modeling/prefer-project-base-class` | family prefix |
| SMT354 | `prefer-domain-exception` | `modeling/prefer-domain-exception` | family prefix |
| SMT355 | `duplicate-class-name` | `modeling/duplicate-class-name` | family prefix |
| SMT356 | `duplicate-narrowing-assert` | `idioms/duplicate-narrowing-assert` | family prefix |
| SMT357 | `missing-duration-unit` | `modeling/implicit-duration-unit` | renamed |
| SMT358 → SMT357 | `prefer-named-duration` | `modeling/implicit-duration-unit` | merged |
| SMT359 | `nested-conditional-expression` | `complexity/forbidden-nested-conditional` | family prefix |
| SMT360 | `mutable-field-in-frozen-dataclass` | `modeling/mutable-field-in-frozen-dataclass` | family prefix |
| SMT361 | `parallel-record-fields` | `modeling/parallel-record-fields` | family prefix |
| SMT362 | `loop-invariant-collection` | `idioms/loop-invariant-collection` | family prefix |
| SMT363 | `complex-comprehension` | `complexity/excess-comprehension-loops` | family prefix |
| SMT364 | `constructor-logic` | `complexity/excess-constructor-decisions` | family prefix |
| SMT365 → SMT339 | `cross-module-private-attribute` | `visibility/foreign-private-access` | merged |
| SMT366 → SMT340 | `comprehension-in-call-argument` | `complexity/inline-call-argument` | merged |
| SMT367 | `excess-private-definitions` | `visibility/excess-private-definitions` | family prefix |
| SMT401 | `test-location` | `tests/misplaced-test` | renamed |
| SMT420 | `private-test-access` | `tests/private-access` | renamed |
| SMT421 | `forbidden-test-mock` | `tests/forbidden-mock` | renamed |
| SMT422 | `string-patch-target` | `tests/string-patch-target` | family prefix |
| SMT423 | `excessive-test-patches` | `tests/excess-patches` | renamed |
| SMT424 | `test-without-outcome-check` | `tests/missing-outcome-check` | renamed |
| SMT425 | `test-only-checks-possibly-empty-data` | `tests/vacuous-assertion` | renamed |
| SMT426 | `unbounded-test-wait` | `tests/unbounded-wait` | renamed |
| SMT427 | `independent-test-case-loop` | `tests/independent-case-loop` | renamed |
| SMT428 | `async-mock-without-await-check` | `tests/missing-await-assertion` | renamed |
| SMT429 | `test-only-public-function` | `visibility/test-only-public-function` | family prefix |
| SMT430 | `test-only-public-attribute` | `visibility/test-only-public-attribute` | family prefix |
| SMT431 | `nondeterministic-test-data` | `tests/nondeterministic-data` | renamed |
| SMT901 | `unused-suppression` | `meta/unused-suppression` | family prefix |
| SMT902 | `suppression-without-reason` | `meta/missing-suppression-reason` | renamed |
| SMT903 | `resolved-debt` | `meta/resolved-debt` | family prefix |

### Configuration model

**Breaking: one definition and one activation rule per option.** Source
conventions were split between options under `structure` and package contracts.
Some options existed at both levels, and some enabled their rule while others did
not.

- **Shared options.** `style` and package overrides now share one set of
  source-convention options: `imports`, `max_module_imports`,
  `module_import_exemptions`, `module_docstrings`, `future_annotations`,
  `private_modules`, `private_attribute_boundary`, `ordering`, `type_dispatch`,
  `max_positional_parameters`, `max_comprehension_loops`,
  `max_constructor_decisions`, `max_private_definitions`,
  `call_argument_kinds`, `str_enum_values`, `dataclass_options`, `bases` and
  `errors`. A value under `style` applies to the whole project. The closest
  package override that sets the same option takes precedence.
- **Package-only options.** Set `required`, `public`, `min_imports_to_bundle`,
  `min_importers_to_export` and `count_tests` in `architecture.packages`.
  Set package-specific `rules` in `style.overrides`.
- **Uniform activation.** An option that belongs to a single rule now enables
  that rule wherever the option applies. Direct `style` options apply to the
  whole project and override options apply to matching packages. This is new for:
  - `private_attribute_boundary` (SMT339)
  - `call_argument_kinds` (SMT340)
  - `str_enum_values` (SMT341)
  - `max_comprehension_loops` (SMT363)
  - `max_constructor_decisions` (SMT364)
  - `max_private_definitions` (SMT367)

  Each of these previously only configured its rule. `ordering` still only
  configures `layout/definition-order`, and `module_import_exemptions` only modifies
  SMT110.
- **Activation scope.** An activation that comes from a package override option
  now applies only to that package. Before, for example, a contract's
  `max_module_imports` enabled SMT110 and the rule then fell back to the global
  limit elsewhere.
- **New built-in defaults.** Selecting SMT345 `complexity/excess-positional-parameters`
  in `rules` or with `--select` now checks with a limit of 5 where no option is
  set. Selecting SMT110 `imports/excess-module-imports` now checks with a limit
  of 20. Before, selecting either rule without an option checked nothing.
- **Unset globals are null.** `smelt config show` reports unset project-wide
  options as `null` instead of their former defaults (1, 2, 12, `module`,
  `explicit` and the full kind and exemption lists). The defaults are unchanged
  and documented in the schema and in
  [structure.md](docs/structure.md#project-wide-options-and-package-overrides).
- **Conflict detection.** Two equally specific overrides that set different
  values of any shared option for the same package are now a configuration
  error. Before, only import style, docstring, future-annotation, private-module,
  private-attribute, ordering and type-dispatch policies were checked.
- **Message wording.** Findings name a project-wide setting as `style`, for
  example `style forbids module docstrings`, and a named package policy as
  `contract "name"`. The SMT345 message now reads `...; contract "api" allows 2`,
  `style allows 3` or `default allows 5`, and its payload `contract` is
  `style` for a project-wide limit and `null` for the built-in default.

### Explicit rule selection

- The former `information-hiding` group has been removed. Use SMT316, SMT320,
  SMT335, SMT429 and SMT430 individually for the same selection.

- Enable each rule by name or SMT code. A named `on` uses the catalog severity.
  Families and `ALL` can disable or cap already-active rules. A broad
  severity with no active matching rules gives a migration error. CLI
  exploration accepts all selectors.
- Local `extends` files apply as ordered rules layers. The built-in
  `smelt:recommended` preset was removed before release. Use
  `smelt init --maintainability` for explicit choices and commented optional
  hints. Discover unconfigured rules with `smelt rules --unconfigured`.
- Broad selections leave SMT306 at error in strict coverage. In package
  overrides, broad settings only apply to source rules.
- **Breaking: concise lines name the rule.** `--format concise` prints
  `path:line:col CODE family/finding message -> repair`. Scripts that parse
  the message after the code must skip the rule name.
- **Breaking: `--statistics` counts hints.** Requested statistics now include
  hint rows without `--show-hints`. The automatic statistics after a long text
  listing still follow `--show-hints`.
- **Schema.** The editor schema completes `ALL` and every family at project
  level, `ALL` and the families with source rules in package overrides, and
  offers `on` for individual names and codes.

### uv workspaces

One root configuration now checks a uv workspace as one project, and each
member can refine it with its own `smelt.yml`. Existing configurations are
unaffected until they set `project.workspace`. See
[uv workspaces](docs/configuration.md#uv-workspaces).

- **`project.workspace: uv`.** Members come from `tool.uv.workspace.members` and
  `exclude` in `pyproject.toml`. Root `source_roots` and `test_roots` apply
  inside every member. Without `source_roots`, each member uses `src/` when it
  holds a package and otherwise the member directory. Root-level test roots are
  always included, root source roots only when the root `pyproject.toml` has a
  `[project]` table. `root_packages` defaults to every member's packages, and
  two members with the same top-level package name are a configuration error.
- **Member configs.** A member's `smelt.yml` or `smelt.yaml` inherits the root
  implicitly and may set `rules`, `style` (options and `overrides`),
  `ignore`, `project.exclude`, `debt` and `architecture`. Its paths are member-relative. `rules` apply
  by path to the member's sources and tests, after the root layers and before
  the closest package override. Direct member `style` options apply to each
  of its packages. Named overrides and package API declarations must match
  the member's own packages. A member cannot set `extends`,
  `architecture.import_graph`, `architecture.canonical_exports`, `architecture.members`, `tests`, `suppressions` or other
  `project` keys, and cannot lower SMT306 under strict coverage. Errors name the member
  file and its own key.
- **Member checks.** `smelt check` in a member directory, or
  `--config <member>/smelt.yml`, loads the workspace root and checks only that
  member. `smelt config show --sources` lists member files, and `origins` map
  `members.<path>.*` keys to them. The policy hash covers member configs.
- **Member architecture.** A member's architecture layout governs the member's
  packages. The root layout governs only packages owned by the root project.
  Members declare their own layers and features. Strict coverage applies to
  members with an empty layout as well. Every module named by a member layout
  must belong to that member. Layers without features span only its packages.
  SMT101-SMT106, SMT301 and SMT305 run once per architecture and report only
  the files it governs. Use `architecture.members` to govern imports between
  members. `smelt context` shows the
  governing member's layers, missing modules name the member file, and the
  coverage report adds `member_architectures` (member path to packages).
- **Test options** remain root-only. Member `rules` still apply to that member's tests.

### Internal

- `smelt.config.models.PackagePolicy` holds the shared options.
  `PackageContract` and `StructureConfig` inherit from it. `POLICY_DEFAULTS`
  holds the built-in defaults.
- `ContractIndex.policy_for` falls back to the `structure` values.
  `ContractIndex.threshold` returns an effective limit and its source.
- `PolicyActivation.option` limits an activation to modules where that option is
  set.
- `smelt.diagnostics.violation.Category` now enumerates the families.
- Merged rule classes: `ModuleDocstringConvention`, `ForbiddenBaseClassAffix`,
  `FileNameConvention`, `StrEnumValueConvention`, `ImplicitDurationUnit`
  (`structure/durations.py`), `ForeignPrivateAccess`
  (`structure/private_access.py`) and `InlineCallArgument`
  (`structure/call_arguments.py`).
- `smelt/rules/renames.py` records retired names and codes for configuration
  errors.
- `smelt.config.models.SeverityName` became `RuleSetting` and includes `on`.
  `SmeltConfig.rule_layers` holds the rules map of every composed file, base
  first; `SmeltConfig.rules` remains the merged view for display.
- uv member discovery moved to `smelt.config.workspace.member_directories`.
  `smelt.analysis.workspace.workspace_members` builds on it. Member configs are
  `SmeltConfig.members` (`MemberConfig`), filled by the loader and never read
  from the root YAML. The selected member is `LoadedConfig.member`.
- `RuleSettings.severity` takes the finding's `path` so member rules also reach
  files outside a source module.
- `MemberConfig.architecture` and `MemberConfig.packages`.
  `smelt.config.workspace.architecture_scopes` returns one `ArchitectureScope`
  (member, config, packages) per architecture, root first, or nothing when no
  member declares one; `scope_for_path` picks the innermost.
- `ArchitectureModel.build` takes `scope`, and `ArchitectureModel.combine` joins
  scoped models into `parts`. `part_for(module)` returns the governing part,
  `owns` and `roots` describe a scope, and `info` answers from the owning part.
  `AnalysisContext.model` is combined when scopes exist, and
  `AnalysisContext.architecture_view(scope)` returns a context whose config and
  model are that scope's.
- `Rule.architecture_scoped` (also in rule metadata and `catalog.json`) marks
  rules that read the architecture config. They run once per scope and keep the
  findings that scope governs.
- `smelt.config.validation` validates every architecture scope against its own
  packages and reports member issues under `members.<path>.architecture`.

## [0.12.0] - 2026-11-16

Report schema 2, YAML version 1, grouped debt baseline version 2, the default
rule set and exit codes remain unchanged. The two new reviews require explicit
selection and have no automatic rewrites. Update installation, hook and CI pins
together to `0.12.0` (`v0.12.0`), refresh the editor schema and reinstall the
bundled agent skill with `smelt skill install --force`. No baseline rewrite is
required. See the [upgrade checklist](docs/upgrading.md#0111-to-0120).

- Opt-in hint [SMT366](https://github.com/mathisarends/smelt/blob/v0.12.0/docs/rules/SMT366.md) `comprehension-in-call-argument`
  asks for named intermediate collections when list, set or dict comprehensions
  are passed directly to a call. Enable the rule explicitly; generator expressions
  and conditional/repeated expression contexts are skipped. No automatic rewrite:
  preserve callable lookup and argument evaluation order.
  Implements [#180](https://github.com/mathisarends/smelt/issues/180).

- Opt-in hint [SMT367](docs/rules/VIS007.md) `excess-private-definitions`
  reviews modules with more than twelve private classes, functions and PEP 695
  type aliases. Enable the rule explicitly; `structure.max_private_definitions`
  supports global and closest-package limits, including zero. Findings list
  definitions and their locations, with private constants as informational context.
  The count does not prove that a cohesive implementation needs splitting; no
  automatic rewrite. Implements [#181](https://github.com/mathisarends/smelt/issues/181).

## [0.11.1] - 2026-11-16

Report schema 2, YAML version 1, grouped debt baseline version 2, the default
rule set and exit codes remain unchanged. Update installation, hook and CI pins
together to `0.11.1` (`v0.11.1`) and refresh the bundled agent skill with
`smelt skill install --force`. Review newly detected test imports under existing
package policies; see the [upgrade checklist](docs/upgrading.md#0110-to-0111).

- Package API checks now include discovered tests and `conftest.py`, including
  configured test roots inside a source package. [SMT107](docs/rules/ARC007.md)
  enforces public entry points, [SMT108](docs/rules/IMP001.md) reviews configured
  bundling thresholds, and opt-in [SMT319](docs/rules/IMP004.md) suggests existing
  package re-exports. Existing policies can now report previously missed test
  imports; no new setting or automatic rewrite. SMT109's `count_tests` opt-in
  remains unchanged. Fixes [#177](https://github.com/mathisarends/smelt/issues/177).

## [0.11.0] - 2026-11-16

Report schema 2, YAML version 1, grouped debt baseline version 2, the default
rule set and exit codes remain unchanged. The four new reviews require explicit
selection or package policy and have no automatic rewrites. Update installation,
hook and CI pins together to `0.11.0` (`v0.11.0`), refresh the editor schema and
reinstall the bundled agent skill with `smelt skill install --force`. No baseline
rewrite is required. See the [upgrade checklist](docs/upgrading.md#0100-to-0110).

- Optional [SMT110](docs/rules/IMP002.md) `module-import-fanout` counts distinct
  direct imports across all project packages. Configure `structure.max_module_imports`
  or a closest-package threshold to activate the hint. Initializers and declared
  composition roots are exempt by default; `module_import_exempt: []` includes them.
  Findings group imports by package and preserve dependency identities for changed
  checks and baselines. No automatic rewrite.
  Implements [#168](https://github.com/mathisarends/smelt/issues/168).

- Opt-in warning [SMT365](https://github.com/mathisarends/smelt/blob/v0.11.0/docs/rules/SMT365.md) `cross-module-private-attribute`
  reports private field/method accesses through statically known project receivers
  owned by another module. Enable the rule explicitly. Global/package
  `private_attribute_boundary: package` permits immediate-package cooperation;
  same-module and own/inherited receiver accesses remain valid. Unknown types and
  ambiguous inheritance are skipped; no automatic rewrite.
  Implements [#170](https://github.com/mathisarends/smelt/issues/170).

- Opt-in review [SMT363](docs/rules/CPX006.md) `complex-comprehension`
  reports excessive iteration clauses, nontrivial conditional elements and
  compound filters, once per outermost comprehension. Enable the rule explicitly;
  `structure.max_comprehension_loops` and package overrides allow deliberate
  flattening. No automatic rewrite; preserve generator laziness.
  Implements [#167](https://github.com/mathisarends/smelt/issues/167).

- Opt-in review [SMT364](docs/rules/CPX007.md) `constructor-logic` reports
  `__init__`/`__post_init__` methods above a configurable decision limit, with the
  counted node locations. Enable the rule explicitly; the global
  `structure.max_constructor_decisions` defaults to 2 and supports closest-package
  overrides, including zero. Nested helper bodies are excluded. No automatic rewrite.
  Implements [#166](https://github.com/mathisarends/smelt/issues/166).

## [0.10.0] - 2026-11-16

Report schema 2, YAML version 1, grouped debt baseline version 2, the default
rule set and exit codes remain unchanged. The two new reviews require explicit
selection and have no automatic rewrites. Update installation, hook and CI pins
together to `0.10.0` (`v0.10.0`), refresh the editor schema and reinstall the
bundled agent skill with `smelt skill install --force`. No baseline rewrite is
required. See the [upgrade checklist](docs/upgrading.md#090-to-0100).

- Optional design review [SMT361](docs/rules/MOD012.md) `parallel-record-fields`
  reports declared sequence fields of the same instance paired with `zip`, once
  per class and field group, with declaration and use locations. It recognizes
  import aliases, simple field aliases and `strict=True`. Local sequences and
  different receivers remain valid; deliberate column storage can keep reasoned
  suppressions. Enable `rules: {parallel-record-fields: hint}`; no automatic rewrite.
  Implements [#155](https://github.com/mathisarends/smelt/issues/155).
- Optional performance review [SMT362](docs/rules/IDM008.md)
  `loop-invariant-collection` groups repeated membership-set comprehensions in
  passive `for` loops when tuple literals or explicit immutable tuple/frozen-record
  declarations establish stable inputs. Unknown calls, mutable inputs, properties
  and suspension points stay quiet. Enable `rules: {loop-invariant-collection: hint}`.
  No automatic hoisting: preserve guards, empty loops and evaluation order. The
  initial scope requires local standard-library dataclasses and plain field types;
  Pydantic snapshots and `Annotated` fields remain outside its inference limits.
  Initial scope for [#154](https://github.com/mathisarends/smelt/issues/154).

## [0.9.0] - 2026-11-16

Report schema 2, YAML version 1, grouped debt baseline version 2, the default
rule set and exit codes remain unchanged. The thirteen new reviews require
explicit selection or package policy and have no automatic rewrites. Update
installation, hook and CI pins together to `0.9.0` (`v0.9.0`), refresh the editor
schema and reinstall the bundled agent skill with `smelt skill install --force`.
No baseline rewrite is required. See the
[upgrade checklist](docs/upgrading.md#080-to-090).

- Optional ownership review [SMT360](docs/rules/MOD011.md)
  `mutable-field-in-frozen-dataclass` reports directly annotated `dict`, `list`
  and `set` instance fields on explicitly frozen dataclasses, including import
  aliases. Review mutability and caller aliases; `Mapping` alone freezes nothing
  and a mapping proxy needs a defensive copy to isolate the caller's dictionary.
  Enable `rules: {mutable-field-in-frozen-dataclass: warning}`; no automatic rewrite.
  Implements [#153](https://github.com/mathisarends/smelt/issues/153).
- Optional style review [SMT359](docs/rules/CPX005.md)
  `nested-conditional-expression` reports direct nesting in either result branch,
  once per outermost chain. Use explicit `if/elif` or `match` while preserving lazy
  evaluation. Simple fallbacks and independent conditionals remain valid. Enable
  `rules: {nested-conditional-expression: hint}`; no automatic rewrite. Implements
  [#152](https://github.com/mathisarends/smelt/issues/152).
- Optional review [SMT348](docs/rules/IDM005.md) `missing-interface-docstring`
  reports `@abstractmethod` and `typing.Protocol` methods whose body is only
  `...`, `pass` or `raise NotImplementedError` and which have no docstring, so
  ports state their contract where implementers read it. Default
  implementations, `@overload`/`@override` signatures, property setters,
  dunders other than `__call__` and tests are ignored. Enable it with
  `rules: {missing-interface-docstring: hint}`; no automatic rewrite.
  Implements [#127](https://github.com/mathisarends/smelt/issues/127).
- Optional review [SMT349](docs/rules/MOD005.md) `stateful-dataclass` reports
  a `@dataclass` that is not `frozen=True` when a method other than
  `__post_init__` assigns, deletes or augments an attribute of `self`, writes
  an item of a field, calls a mutating container method on a field declared as
  a mutable container, or calls `setattr` on `self`. Build values before
  construction and freeze the dataclass, or keep accumulation in a plain class
  with private attributes. Mutations through other objects and tests are
  ignored. Enable it with `rules: {stateful-dataclass: hint}`; no automatic
  rewrite. Implements [#133](https://github.com/mathisarends/smelt/issues/133).
- Optional review [SMT350](docs/rules/IDM006.md) `missing-broad-except-reason`
  reports `except Exception`, `except BaseException`, tuples containing them
  and bare `except:` handlers without a comment explaining why they catch
  everything. Comments on the `except` line, directly above it or before or on
  the first body statement count; linter directives such as `noqa` or
  `pylint: disable` alone do not. Handlers that always re-raise and tests are
  ignored. Enable it with `rules: {missing-broad-except-reason: hint}`; no
  automatic rewrite. Implements
  [#143](https://github.com/mathisarends/smelt/issues/143).
- Package contracts accept `dataclass_options`, a list of `frozen`, `slots`
  and `kw_only`. Policy rule [SMT351](docs/rules/MOD006.md)
  `missing-dataclass-option` then reports production `@dataclass` classes
  that do not set each listed option to `True`. Missing `slots` is not
  reported below bases from outside the project or for classes with a
  `functools.cached_property`. The closest package policy wins, `[]` lifts an
  outer requirement and `smelt context` shows the policy. Tests are not
  checked; no automatic rewrite. Implements
  [#137](https://github.com/mathisarends/smelt/issues/137).
- Optional review [SMT355](docs/rules/MOD009.md) `duplicate-class-name`
  reports public top-level classes whose name is also defined at the top level
  of another module in the same package, counting the package's `__init__.py`.
  Each definition gets a finding with the other locations. Re-exports, private
  and nested classes, separate packages and tests are not compared. Enable it
  with `rules: {duplicate-class-name: warning}`; no automatic rewrite.
  Implements [#138](https://github.com/mathisarends/smelt/issues/138).
- Optional review [SMT356](docs/rules/IDM007.md) `duplicate-narrowing-assert`
  reports, once per class and expression, `assert self.<attribute> is not None`
  or `assert isinstance(self.<attribute>, ...)` repeated in at least two methods
  of the same class, including each operand of an `and`. Single local
  narrowings, asserts in nested functions, static methods and tests are not
  reported. Make the attribute required during construction or read it through
  one property that raises a meaningful error. Enable it with
  `rules: {duplicate-narrowing-assert: warning}`; no automatic rewrite.
  Implements [#142](https://github.com/mathisarends/smelt/issues/142).
- Optional review [SMT352](docs/rules/CPX004.md) `duplicate-function` reports
  module-level functions whose parameters, annotations, decorators and body
  match another function in the same root package, ignoring names,
  docstrings, comments and formatting. Every copy gets a finding listing the
  others and their nearest common package. Bodies under three statements,
  methods, nested functions and tests are ignored. Enable it with
  `rules: {duplicate-function: warning}`; no automatic rewrite. Implements
  [#140](https://github.com/mathisarends/smelt/issues/140).
- Optional review [SMT357](docs/rules/MOD010.md) `missing-duration-unit`
  reports module constants, class fields and parameters named `*timeout`,
  `*delay`, `*interval`, `*duration` or `*wait` that hold an `int` or `float`
  without a unit suffix, such as `_FRAME_TIMEOUT = 2.0`. Names ending in a
  unit (`_TIMEOUT_SECONDS`, `quiet_ms`), `timedelta` values, `@overload` and
  `@override` signatures and tests are not reported. Enable it with
  `rules: {missing-duration-unit: warning}`; no automatic rewrite.
- Optional review [SMT358](https://github.com/mathisarends/smelt/blob/v0.9.0/docs/rules/SMT358.md) `prefer-named-duration`
  reports `int` and `float` literals passed as the duration of
  `asyncio.timeout`, `asyncio.sleep`, `asyncio.wait_for`, `asyncio.wait`,
  `time.sleep` and the anyio and trio sleep and timeout scopes, and literals
  added to a clock reading such as `loop.time() + 10`. `sleep(0)` and tests
  are not reported. Enable it with `rules: {prefer-named-duration: warning}`;
  no automatic rewrite. Together with SMT357 implements
  [#135](https://github.com/mathisarends/smelt/issues/135).
- Package contracts accept `bases`, a mapping from an outside base class to
  the project base that replaces it, e.g.
  `{pydantic.BaseModel: app.core.Options}`. Policy rule
  [SMT353](docs/rules/MOD007.md) `prefer-project-base-class` then reports
  production classes that inherit the outside base directly, resolved through
  imports, aliases and module attributes. Only inheritance is judged:
  importing the outside base for annotations, `isinstance` or adapters stays
  valid. The module defining the project base is exempt, the closest package
  policy wins, `{}` lifts an outer mapping and `smelt context` shows the
  policy. Tests are not checked; no automatic rewrite. Implements
  [#139](https://github.com/mathisarends/smelt/issues/139).
- Package contracts accept `errors: {base: <qualified exception>, allow: [...]}`.
  Policy rule [SMT354](docs/rules/MOD008.md) `prefer-domain-exception` then
  reports production code that raises a builtin exception class instead of
  the project hierarchy, including `raise ValueError(...) from error`.
  `NotImplementedError`, `StopIteration`, `StopAsyncIteration`,
  `GeneratorExit`, `KeyboardInterrupt` and `SystemExit` are always allowed and
  `allow` adds more. Bare re-raises, names shadowing a builtin, protocol dunder
  methods such as `__getattr__` and pydantic validators are not reported. The
  closest package policy wins, `errors: {}` lifts an outer policy and
  `smelt context` shows it. Tests are not checked; no automatic rewrite.
  Implements [#141](https://github.com/mathisarends/smelt/issues/141).

## [0.8.0] - 2026-11-14

Report schema 2, YAML version 1, grouped debt baseline version 2, the default
rule set and exit codes remain unchanged. SMT343/SMT344/SMT346/SMT347 are opt-in;
SMT345 activates through an explicit package limit. All five reviews require
manual repairs. Update installation, hook and CI pins together to `0.8.0`
(`v0.8.0`), refresh the editor schema and reinstall the bundled agent skill with
`smelt skill install --force`. No baseline rewrite is required. See the
[upgrade checklist](docs/upgrading.md#070-to-080).

- Optional style review [SMT346](docs/rules/TYP006.md)
  `prefer-deferred-annotations` reports modules that quote annotations such as
  `list["Tab"]`. With the annotations future import or a declared Python 3.14+
  minimum the quotes are redundant; otherwise the hint suggests adding
  `from __future__ import annotations`. A `future_annotations: forbid` package
  contract keeps quotes before 3.14. `Literal` values, `Annotated` metadata,
  runtime strings such as `cast("Tab", ...)` and tests are ignored. Enable it
  with `rules: {prefer-deferred-annotations: hint}`; no automatic rewrite.
  Implements [#123](https://github.com/mathisarends/smelt/issues/123).

- Optional review [SMT347](docs/rules/TYP007.md) `anonymous-tuple-type`
  reports fixed-length tuple types in annotations and type aliases with three
  or more elements or a repeated element type, such as
  `dict[UUID, tuple[UUID, UUID, Connection]]`, and suggests a `NamedTuple` or
  frozen dataclass. `tuple[X, ...]`, two distinct element types, quoted
  annotations, dunder/`@override`/`@overload` signatures and tests are not
  reported. Enable it with `rules: {anonymous-tuple-type: hint}`; no automatic
  rewrite. Implements part of
  [#121](https://github.com/mathisarends/smelt/issues/121).

- Optional review [SMT344](docs/rules/IDM004.md) `redundant-name-alias` reports
  a module-level name that only repeats another class or function name, such as
  `AutofillData = PasswordData`, including `TypeAlias` and `type` statements.
  Unions, subscripts, `Annotated`, constants, conditional fallbacks, typing
  special forms and tests are not reported. Enable it with
  `rules: {redundant-name-alias: hint}`; no automatic rewrite. Implements
  [#120](https://github.com/mathisarends/smelt/issues/120).

- New package policy `max_positional_parameters` limits how many parameters a
  function or method may accept positionally; later parameters must be
  keyword-only. [SMT345](docs/rules/CPX003.md) `excess-positional-parameters`
  reports violations (warning). `self`/`cls`, `*args`/`**kwargs`, dunder,
  `@overload` and `@override` definitions and tests are not counted. Enable it
  with `structure.contracts.<name>: {match: app, max_positional_parameters: 2}`;
  `smelt context` shows the limit. No automatic rewrite. Implements
  [#122](https://github.com/mathisarends/smelt/issues/122).

- Optional review [SMT343](docs/rules/VIS006.md) `redundant-private-delegate`
  reports a method whose whole body returns a call to a private method of the
  same class that receives every parameter unchanged and is used nowhere else,
  such as `def events(...): return self._events(...)`. Validation before the
  call, shared or decorated helpers, dunder methods, classes with bases from
  other modules and tests are not reported. Enable it with
  `rules: {redundant-private-delegate: hint}`; no automatic rewrite. Implements
  [#119](https://github.com/mathisarends/smelt/issues/119).

## [0.7.0] - 2026-11-14

Report schema 2, YAML version 1, grouped debt baseline version 2, the default
rule set and exit codes remain unchanged. The six new rules SMT337–SMT342 are
opt-in; SMT341 and SMT342 are alternative `StrEnum` conventions, so enable at
most one. Text and concise output now label details by name and list shared
repair advice once; JSON, SARIF and GitHub output are unchanged. Update
installation, hook and CI pins together to `0.7.0` (`v0.7.0`), refresh the
editor schema and the installed agent skill. No baseline rewrite is required.
See the [upgrade checklist](docs/upgrading.md#060-to-070).

- Optional style rules fix how `StrEnum` values are declared.
  [SMT341](docs/rules/MOD004.md) `str-enum-auto-values` makes `auto()` the
  convention: it reports explicit strings equal to the lowercased member name,
  and `auto()` members in classes that need custom values such as
  `"mouseMoved"`. [SMT342](https://github.com/mathisarends/smelt/blob/v0.7.0/docs/rules/SMT342.md) `str-enum-explicit-values`
  makes explicit strings the convention and reports every `auto()` member.
  Enable one of them, e.g. `rules: {str-enum-auto-values: hint}`. Tests are not
  checked; no automatic rewrite. Implements
  [#116](https://github.com/mathisarends/smelt/issues/116).

- Repair hints of SMT316, SMT320, SMT335, SMT429 and SMT430 no longer repeat the
  member and its suggested name, which the message and the `suggested_name`
  detail already carry; they name the target only when a collision changes it.
  The hints are therefore shared across findings and listed once. Messages,
  finding ids and debt identities are unchanged.

- Text and concise output list repair advice repeated verbatim by several
  findings of one rule once, under "Repairs shared by several findings", instead
  of after every finding. Unique advice stays with its finding; JSON, SARIF and
  GitHub output still carry the hint on each finding.

- Text output labels finding details by name (`Suggested name: _hits`) instead
  of prefixing every detail with `Expected`, and omits the constant
  `automatic_rename` flag. JSON and SARIF are unchanged.

- Optional [SMT340](docs/rules/CPX002.md) reports an awaited result passed
  directly as a call argument, such as `Result(text=await client.read())`, and
  suggests naming it in a local variable first (the keyword for keyword
  arguments). Enable `rules: {await-in-call-argument: hint}`. Awaits in
  comprehensions, lambdas, conditional expressions, boolean operators and while
  conditions are skipped; tests are not checked; no automatic rewrite. Implements
  [#112](https://github.com/mathisarends/smelt/issues/112).

- Optional [SMT339](docs/rules/VIS005.md) reports production imports and
  module-qualified reads of private symbols defined in analyzed project modules.
  Enable `rules: {private-symbol-import: warning}`. Dunders, public aliases and
  initializer re-exports under public aliases stay valid; tests remain with
  SMT420. Respects the TYPE_CHECKING policy; renames stay manual. Implements
  [#105](https://github.com/mathisarends/smelt/issues/105).

- Optional [SMT338](docs/rules/TYP005.md), enabled with
  `rules: {duplicate-literal-type: hint}`, finds repeated multi-value `Literal`
  types within and across source files in the same root package. Findings group
  locations per file and value set, preserve `Annotated` metadata boundaries,
  and suggest a shared plain type alias. Facts are cached by file content;
  changes to existing groups remain visible in changed checks and debt baselines.
  Same values may describe independent concepts; no automatic rewrite is made.

- Optional [SMT337](docs/rules/TYP004.md) flags `AsyncIterator[T]` return
  annotations on `@asynccontextmanager` functions and recommends
  `AsyncGenerator[T, None]`. Enable
  `rules: {deprecated-async-contextmanager-return: warning}`. The decorator stays
  supported; ordinary async iterators are unaffected. Import aliases and quoted
  annotations are supported; rewrites remain manual.

## [0.6.0] - 2026-11-08

Report schema 2, YAML version 1, grouped debt baseline version 2 and the default
rule set remain unchanged. The six new rules SMT331–SMT336 are opt-in; canonical
filename checks activate through explicit file-naming policy. Existing individual
rule selections remain valid. New `init --maintainability` configurations select
`information-hiding`, adding SMT320 and SMT335 to the previous starter policy.
Update installation, hook and CI pins together to `0.6.0` (`v0.6.0`), refresh the
editor schema and review findings before changing debt. No baseline rewrite is
required for this upgrade. See the [upgrade checklist](docs/upgrading.md#050-to-060).

- Optional [SMT336](docs/rules/LAY009.md) reviews module filenames that repeat an
  ancestor package, such as `cars/application/car_exporter.py`. Enable
  `rules: {redundant-module-name: hint}`. Whole-name prefixes/suffixes and simple
  trailing-s plurals are recognized; occupied shorter names are not suggested.
  Renames and import updates remain manual. Implements
  [#94](https://github.com/mathisarends/smelt/issues/94).

- Enable the five public-API reviews together with
  `rules: {information-hiding: warning}` (SMT316/SMT320/SMT335/SMT429/SMT430).
  Tests alone do not justify public visibility. Individual entries override the
  group in the same map regardless of order; closer package settings still win.
  The group works with CLI selection/ignores and scoped ignores, and is exposed
  in `explain`, catalog JSON and the editor schema. Existing names, codes,
  suppressions and debt identities remain valid. `init --maintainability` now
  writes this group, adding the module-symbol and attribute reviews to its
  generated selection. See [scope and migration](docs/rule-selection.md#information-hiding).

- Optional [SMT335](docs/rules/VIS004.md) reviews public instance and class data
  attributes with no observed access outside their class. Enable
  `rules: {internal-public-attribute: warning}`. Test access preserves visibility
  without duplicating SMT430; exports and uncertain inheritance/framework
  contracts remain protected. Renames stay manual. Implements
  [#96](https://github.com/mathisarends/smelt/issues/96).

- Ship the official `smelt` agent skill with every wheel and source distribution,
  including the complete offline rule, configuration and CLI reference. Install
  with `smelt skill install`; refresh after upgrading with `--force`, which
  replaces local edits and obsolete references. Use `--destination` to choose
  another skill directory. See [agent skill installation](docs/cli.md#agent-skill).
  Generated references are checked for drift in CI and release validation with
  `python scripts/generate.py --check`. The bundle contains consumer guides and
  rule pages; benchmark/evaluation data and maintainer documents stay in the repo.

- Optional [SMT334](docs/rules/LAY008.md) reviews private literal constants such
  as `DownloadController._DOWNLOADABLE_EXTENSIONS` for placement at module scope.
  Enable `rules: {private-class-constant: error}` to block them. Inherited and
  decorated classes retain their attribute contracts; moves and reference updates
  remain manual. Implements [#82](https://github.com/mathisarends/smelt/issues/82).

- Optional [SMT333](docs/rules/IDM003.md), enabled with
  `rules: {prefer-callable-instance: hint}`, reviews classes with one public
  instance method for `__call__` and an operation-based class name. Findings keep
  constructor and call-time signatures separate. Existing callable APIs,
  inheritance and decorated/protocol classes remain valid; no automatic rename.

- Declare canonical YAML extensions and Compose basenames once with
  `structure.file_naming: {yaml_extension: yaml, compose_basename: compose}`.
  [SMT331](docs/rules/LAY007.md) and [SMT332](https://github.com/mathisarends/smelt/blob/v0.6.0/docs/rules/SMT332.md) flag other
  spellings throughout the project, including hidden configuration directories
  and dotted Compose variants. Policies are optional, respect exclusions and
  support staged checks; renames and reference updates remain manual.
  Staged checks reuse YAML candidates from native Git-index capture, with an
  independent flat-inventory fallback, instead of walking virtual directories.

## [0.5.0] - 2026-11-02

Report schema 2, YAML version 1 and the default rule set remain unchanged.
Debt writes now use grouped baseline version 2, which older Smelt versions cannot
read. Existing version-1 baselines remain readable; unscoped SMT321 debt needs
review and explicit re-recording. Update installation, hook and CI pins together
to `0.5.0` (`v0.5.0`) before rewriting baselines, and refresh the editor schema.
See the [upgrade checklist](docs/upgrading.md#040-to-050).

- Contributor checks now require an issue/ticket reference for TODO comments
  through Ruff TD003. Tracked TODOs remain allowed; Smelt's rule catalog is unchanged.

- Document SMT317's activation and developer-facing extraction evidence with
  concrete query-helper regressions. Inherited-class exclusions and the existing
  advisory behavior are preserved; no duplicate static-method rule is added.

- Optional SMT329 (`rules: {caller-before-callee: hint}`) reviews step-down reading
  order for direct same-visibility functions and ordinary methods. Recursive,
  shadowed and definition-time-dependent pairs are retained; SMT313's public-first
  categories take precedence. No definitions are moved automatically.

- Optional SMT328 (`rules: {redundant-dishka-provider: hint}`) reviews Dishka
  provider methods that only forward dependencies to a constructor. Findings
  include a direct registration candidate with recognized decorator options.
  Resource factories and additional logic remain valid; no automatic rewrite.

- Optional SMT327 (`rules: {secret-settings-field: warning}`) reviews secret-named
  Pydantic settings fields accepting plain `str`/`bytes`, including optional and
  annotated types. Correct secret types and nonsecret names remain valid.
  Findings omit default values; no annotations or consumers are rewritten.

- Optional SMT326 (`rules: {no-base-class-suffix: error}`) reviews class definitions
  such as `_MessageBase`. SMT323 retains its existing prefix semantics. Imports,
  aliases and test classes remain outside scope; no automatic rename.

- Optional SMT330 (`rules: {package-only-dunder-all: error}`) restricts module
  `__all__` declarations to package `__init__.py` files. Package overrides and
  reasoned suppressions retain deliberate compatibility exports; no automatic move.

- Optional SMT431 (`rules: {nondeterministic-test-data: warning}`) reviews known
  random/UUID data generators in tests and fixtures. Prefer fixed test inputs or
  seeded local generators; intentional randomness tests can retain reasoned
  suppressions. Production generators are unaffected; no automatic rewrite.

- SMT108 now reviews mixed package-facade and implementation imports with
  `bundle_imports_at: 2`, includes missing export proposals and blocks bundling
  advice when proposed caller/facade edges close a known runtime import cycle.

- Debt baselines now write grouped JSON version 2: repeated rule/path/message
  metadata appears once, while every accepted fingerprint and duplicate occurrence
  remains available for matching. Version-1 files remain readable; `smelt debt
  --prune` upgrades assessed entries without accepting new debt. Use `smelt debt
  --show [PATHS]` for a compact, read-only baseline summary, or add `--format json`
  for the grouped data. [SMT321](docs/rules/TYP003.md) now distinguishes enclosing
  functions in debt and changed checks, so fixing one function cannot silently
  accept an identical new violation in another. Old unscoped SMT321 debt requires
  review and explicit re-recording. See [adoption](docs/adoption.md).

- Package contracts now support `module_docstrings: require`, activating
  [SMT325](https://github.com/mathisarends/smelt/blob/v0.5.0/docs/rules/SMT325.md) `missing-module-docstring` for every analyzed
  production module, including empty package initializers. Empty/blank docstrings
  do not satisfy the requirement. Choose `forbid` for the existing
  [SMT309](docs/rules/LAY002.md) convention; use a reasoned file suppression or an
  explicit nested `allow` policy for exceptions. Defaults remain unchanged.

- Optional [SMT324](docs/rules/MOD003.md), enabled with
  `rules: {prefer-str-enum-discriminator: hint}`, reviews single-string `Literal`
  fields named `type`, `kind` or `tag` for shared `StrEnum` members. Keep narrow
  `Literal[EventType.MEMBER]` annotations and matching enum defaults. This syntax
  review does not prove union membership or rewrite code; Python targets before
  3.11 and general multi-value Literals remain unchanged.

## [0.4.0] - 2026-11-01

Report schema 2, YAML version 1 and the default rule set remain unchanged.
Custom rules must update `BaseRule` imports to `Rule` and annotations for the
former `Rule` protocol to `RuleContract`. Update installation, hook and CI pins
together to `0.4.0` (`v0.4.0`) and refresh the editor schema. See the
[upgrade checklist](docs/upgrading.md#030-to-040).

- Use `.yml` consistently in configuration examples, fixtures and maintainer
  scripts. `smelt init` now creates `smelt.yml`, which takes precedence when both
  supported filenames exist. Existing `smelt.yaml` files still work. The published
  `.pre-commit-hooks.yaml` manifest retains its required filename. Integration
  instructions consistently use prek.

- Optional [SMT323](docs/rules/MOD002.md), enabled with
  `rules: {no-base-class-prefix: error}`, rejects class definitions such as
  `BaseRepository` and `_BaseRepository` in favor of responsibility-based names.
  Imports, third-party inheritance, `Baseline` and `Base64` remain valid. Package
  rule overrides can disable the convention; checks never rename symbols.
  Smelt enables the convention for its own sources; the default set is unchanged.
- Smelt also enables the opt-in `deep-object-navigation` Law of Demeter review
  for its own sources. Facade analysis receives its file inventory directly,
  and filesystem/Python-target analysis receives its source store explicitly.
  SMT322 recognizes statically evidenced module/data namespaces and known value
  APIs, preserving unknown collaborator findings. Changes to imported record
  definitions are considered on every check; no code is executed or rewritten.
  Unproven namespaces remain advisory review candidates, with known receiver
  types and the unresolved step included for agent API-contract review. A finding
  alone does not require an architecture change.
- Rule implementations now inherit `smelt.rules.base.Rule` instead of `BaseRule`.
  The former structural `Rule` protocol is named `RuleContract`; lazy rule
  implementations retain the same interface. Update custom rule imports and
  type annotations accordingly.
- Independent workspaces can share a local policy fragment with `extends` and
  explicit child overrides. Mappings merge; lists replace, including `[]`.
  Project paths remain relative to the selected workspace. `config show --sources`
  explains effective inputs and origins; staged checks and cache dependencies
  include all bases. Both published hooks now run on every commit, including
  policy-only edits. See [shared configuration](docs/configuration.md#shared-local-policy).

- New opt-in [SMT322](docs/rules/CPX001.md) `deep-object-navigation` reviews
  method calls reached through multiple stored instance attributes, such as
  `self.browser.input.motor().aim()`. Enable with
  `rules: {deep-object-navigation: hint}` or `--select SMT322`; package rule
  overrides and inline suppressions preserve intentional navigation. Direct
  collaborator calls, pure data reads and direct-collaborator fluent chains
  remain quiet. This bounded syntax review has no automatic rewrite.
- [SMT901](docs/rules/MET001.md) diagnoses unknown inline suppression identifiers,
  including readable rule names where stable SMT codes are required. Known
  inactive rules retain their narrowed-check behavior.
- [SMT426](docs/rules/TST008.md) groups shared-helper operations by one repair in
  text and adds deterministic `shared_helper` JSON groups. Individual findings,
  identities, counts, suppressions and debt remain unchanged. [SMT424](docs/rules/TST006.md)
  explains known helper inference limits at the affected call sites.
- [SMT104](docs/rules/ARC004.md) adds the opt-in `cycles: [modules]` scope and
  independent `type_checking_cycles: ignore`. Existing scopes are explicitly
  labeled as aggregated; witnesses include edge phases. Default scopes stay unchanged.
- [SMT109](docs/rules/ARC008.md) accepts optional, statically verified
  `structure.canonical_exports` ownership and distinguishes same-root/other-root
  importers. Existing exports inform the review; frequency alone never expands API.
- Literal lazy-export tables with a matching guarded `import_module`/`getattr`
  forwarder now contribute export identity and visibility evidence. Unsupported
  dynamic handlers, escaped/mutated tables and type stubs alone remain unverified.
- [SMT313](docs/rules/LAY004.md) permits bounded ordering fixes around unrelated
  nested-function dictionaries and `nonlocal` closure writes. Reflection on
  unknown receivers or ordered namespaces retains its manual-review barrier.

- New opt-in [SMT321](docs/rules/TYP003.md) `implicit-optional-default`
  reviews `parameter or fallback` for explicitly optional parameters with a
  `None` default. Enable with `rules: {implicit-optional-default: warning}` or
  `--select SMT321`. Use `--fix --unsafe-fixes` to replace it with an explicit
  `is not None` conditional after reviewing intentional falsy defaults. Plain
  `--fix` preserves existing truthiness behavior.

## [0.3.0] - 2026-11-01

### Upgrade first

**JSON consumers need an update.** Check and error documents now use schema 2.
Request `--show-coverage` for full coverage details and resolve
`expected.usage_scope_index` through the report's `usage_scopes` array. This is
a report-format change; YAML stays `version: 1`, and default rules, exit codes,
SMT codes and debt identities stay unchanged. See the
[upgrade checklist and compatible parser example](docs/upgrading.md).

Update tool, dependency, hook and CI pins together to `0.3.0` (Git tag `v0.3.0`),
then refresh `.smelt.schema.json`. Upgrading from 0.1.x also requires the
[configuration migration](docs/configuration.md#breaking-change-migration).

### New capabilities

| Capability | Enable / use | Details |
| --- | --- | --- |
| Review widely imported names missing from a package facade | Set a contract's `export_at` threshold; opt-in, hint severity | [SMT109](docs/rules/ARC008.md) |
| Review internal public constants and PEP 695 aliases | `rules: {internal-public-module-symbol: warning}`; opt-in | [SMT320](docs/rules/VIS002.md) |
| Get one-line findings with repair advice | `smelt check --format concise` | [CLI and reports](docs/cli.md) |
| Migrate existing installations and report consumers | Follow the release-specific checklist | [Upgrading](docs/upgrading.md) |

### Ordering and module visibility

- SMT313 now recognizes unions of unshadowed builtin types and `None` in eager
  function annotations for declared Python 3.10+ targets, so a helper annotated
  `str | bytes` no longer silently blocks public-first ordering on Python 3.12/3.13.
  Imported/user-defined operands, shadowed names, explicit namespace mutations and unions in defaults remain
  execution barriers. Inspect barriers with `check --format json --show-coverage`.
- New opt-in [SMT320](docs/rules/VIS002.md) `internal-public-module-symbol`
  reviews single, unconditional uppercase module assignments and PEP 695 type
  aliases with no observed external use or public-signature evidence. Enable it
  with `rules: {internal-public-module-symbol: warning}` or `--select SMT320`.
  Exports, imports/re-exports, test accesses, escaped modules and public annotation
  dependencies preserve names. Dynamic namespaces and uncertain bindings are
  skipped; external consumers remain unassessed. No automatic renaming.
- SMT311/SMT313 explanations clarify that Python 3.12/3.13 projects can retain
  an annotations future import for forward references in their chosen public-first
  order. Eager dependencies still take priority when the import is absent. `Self`
  requires a subclass-preserving return contract; no automatic replacement of a
  matching class-name annotation is suggested. See [definition ordering](docs/structure.md#definition-order-with-execution-dependencies).

### Test evidence and package contracts

- Opt-in SMT424/SMT425/SMT426 outcome and deadline reviews follow one direct call
  to an unambiguous, undecorated, non-generator helper in the same file, including local async
  helpers executed with `await` or `asyncio.run`. Assertions retain empty-loop
  review, and hidden unbounded operations report their helper and call site.
  Caller async deadlines guard async waits, never synchronous subprocesses.
  Uncalled definitions, unexecuted coroutines, callbacks, imported helper bodies
  and further helper calls remain outside inference; configured assertion helpers
  continue to work. No analyzed code is executed.
- Overlapping test roots consistently assign a test to the most specific declared
  root, independent of configuration order. Mirror checks retain separate
  workspace-member roots.
- Package contracts can set `export_at` for SMT109 `facade-export-gap`: one
  advisory finding per widely imported implementation name missing from its
  facade, with definition location, importer lists, other re-exports and a bounded
  cycle check. Tests count only with `count_tests: true`; type-checking-only
  imports and uncertain exports are skipped. No automatic API or import changes.
  See [facade reviews](docs/structure.md#suggestions-to-bundle-implementation-imports).
- Overlapping SMT108/SMT109 thresholds now choose the most specific pattern per
  concrete package, removing duplicate hints and allowing exact overrides of
  wildcard contracts. Equally specific conflicting settings are config errors.

### Reports, suppressions and fixes

- **JSON report schema 2:** `check --format json` emits a small coverage summary
  by default; `--show-coverage` restores full policy details and the per-file
  inventory. Repeated visibility boundaries move to report-level `usage_scopes`,
  referenced by each finding's `expected.usage_scope_index`. Scope, comparison,
  warnings and exit thresholds remain available. See [report compatibility](docs/cli.md).
- `check --format concise` prints one line per finding with repair advice.
  Text omits empty details, prints visibility limits once, and groups related
  late-import SMT313 findings with their affected lines. JSON keeps individual
  findings plus an `ordering` group; debt fingerprints are retained.
- Inline suppressions follow logical continuation lines, including interior
  comments in multiline assertions, formatted calls and signatures. Interior
  import-name exceptions stay local and never cover neighboring names, the next
  statement or a function body. Unused-suppression checks remain active. See
  [suppression scope](docs/adoption.md).
- Visibility rules preserve public members of classes exported through static
  package facades or `__all__`, including aliases and re-export chains; cache
  invalidation tracks export changes. SMT429 distinguishes functions referenced
  only by tests from helpers also used inside their module, without claiming
  unassessed external consumers do not exist.
- Cache directories ignore their own contents. Init reports the number of tests
  matching a mirror pattern, and ordinary text checks omit the policy coverage
  banner without architecture (strict coverage still applies). Hook documentation
  uses a pinned wheel installation and explains source-build requirements.
  SMT311/SMT318 explanations clarify decorated annotations and Literal-typed
  boundaries; SMT426's subprocess example explicitly sets `check=False`.
- Release checks enforce matching Python/native/lockfile versions, dated release
  notes and current installation pins. Publication also waits for installation
  smoke tests of the exact Linux x86_64, Windows x86_64 and macOS aarch64 wheels.

## [0.2.0] - 2026-10-31

- Visibility reviews (SMT316/SMT317/SMT429/SMT430) explicitly limit their claims to
  analyzed callers and expose `usage_scope`, including exclusions and unassessed
  maintainer scripts, unselected packages and external consumers. They do not
  scan excluded code or infer that unobserved callers cannot exist.
- SMT313 fix refusals identify the triggering expression and source location in
  `fix_blocker` and the human-readable reason, including inherited/decorated
  classes, namespace inspection and nonliteral type variables. Existing safety
  refusals are retained; skipped-fix output carries the same location.
- SMT425 recognizes locally assigned nonempty list/set/dict literals, including
  annotations and aliases, and discards that evidence after possible mutations,
  calls or branch merges. SMT424/SMT425 also recognize direct assertions in
  statically known, checked `sys.executable -c` subprocesses. Unchecked exits,
  optimized Python, dynamic scripts, files and potentially swallowed failures
  remain outside recognition; subprocess review hints explain that boundary.
- Smelt now checks its own sources with public-first ordering, internal-member
  visibility, test-only public API reviews, when-needed future annotations and
  test-quality reviews. CI and release validation fail on warnings too. Internal
  helpers are private, obsolete snapshot helpers are removed, and deliberate
  primitive/cache tests carry local, reasoned exceptions. Consumer defaults stay
  unchanged.
- SMT420 locates private imports on the imported name, including aliases in
  multiline imports. A reasoned exception on that name now survives Ruff
  formatting and does not suppress neighboring import lines; put multiline
  import exceptions next to the affected name.
- Optional [SMT319](docs/rules/IMP004.md), enabled with
  `rules: {prefer-package-import: hint}` or `--select SMT319`, suggests existing
  package re-exports so callers can combine imports through a common `__init__.py`.
  All names must have direct, unconditional public re-exports in analyzed code;
  internal package imports and unscanned third-party packages are excluded.
  Findings preserve aliases in their suggestions; checks never rewrite imports.
- Worker transport now acknowledges requests within 250 ms and bounds accepted
  responses to five seconds, including partial output. Failures fall back to
  foreground checking without repeating accepted requests. A watchdog and four
  OS-owned worker slots bound background sessions; terminated workers release
  capacity. Whole-process memory observations include helpers and Windows
  launchers; the retained-state budget is not a total-process memory quota.
- Default cached staged checks read HEAD, the index and Git objects through a
  persistent native reader, avoiding Git child-process starts. The reader supports
  SHA-1/SHA-256, packed objects, linked worktrees, split indexes and alternate
  commit indexes. Unsupported repositories, other Git overrides and reader
  failures fall back to Git; `--no-cache` keeps independent reads. Workers also
  retain bounded member dependency state, validating current inputs before reuse
  and discarding borrowed state on failed updates. Worker requests are sent as
  complete TCP frames. See the [paired CLI and complete-hook measurements](https://github.com/mathisarends/smelt/blob/main/docs/agent-workflow.md).
- Staged checks reuse verified policy blobs before parsing configuration, avoiding
  a redundant Git blob subprocess after source edits. Policy changes still use
  their captured object ID; `--no-cache` keeps independent reads. Paired Windows
  CLI edit medians improve about 11%; complete-hook gains remain inconclusive.
- The installed CLI now uses a native Rust client and bounded local workers for
  read-only checks. Fresh clients reuse imports, while every check captures current
  Git/config inputs. CI, fixes and other commands stay in the foreground; worker
  startup/transport failures fall back to checking. Workers restart when analyzer
  identity changes and expire after 120 idle seconds. Source installs, including
  pre-commit environments from Git, now require Rust; release wheels include the
  compiled client. `python -m smelt` retains the direct Python entry point.
- Member-local checks maintain a persistent reverse dependency index and replace
  only changed usage contributions. Unchanged decisions can be reused without
  loading all definition objects. Removed callers, re-exports, missing namespaces,
  inheritance and inventory changes remain part of invalidation.

- Checks select built-in rules using a generated lightweight catalog, loading only
  the needed implementations. Staged results can survive edits to unobserved
  content while preserving the current index hash in JSON/SARIF; additions,
  deletions, observed source, config, metadata and debt still invalidate them.
  Normal cached staged retries capture repository/HEAD and index in two Git
  invocations. A separate benchmark measures a retained Python worker including
  fresh client startup and IPC; it preceded the native client above.
  See [the performance measurements](https://github.com/mathisarends/smelt/blob/main/docs/agent-workflow.md).
- Staged checks read captured Git blobs directly instead of writing temporary
  source trees. An unchanged retry can reuse its exact result before loading the
  analysis engine. Per-file import, member and test facts and selected findings
  are reused across edits, with dependency-aware invalidation for callers,
  re-exports and fixtures. `--no-cache` remains an independent check. See the
  [paired CLI benchmarks and cache limits](https://github.com/mathisarends/smelt/blob/main/docs/agent-workflow.md).
- `architecture.imports.transitive: true` now checks arbitrary import chains for
  layer, cross-feature, shared, composition-root and third-party policies, e.g.
  `domain → shared.utils → sqlalchemy`. Findings include a deterministic shortest
  chain with each hop's source line; `TYPE_CHECKING` policy applies at every hop.
  The opt-in now follows all reachable module imports, including allowed layers
  and unused facade exports, rather than just selected re-exports. External
  packages remain leaves and the default is still direct imports only. Smelt's
  own repository policy enables these checks. See
  [SMT101](docs/rules/ARC001.md) and [SMT103](docs/rules/ARC003.md).
- **Breaking configuration API:** fixed source/test checks are enabled directly by
  problem name in `rules`, with one value for activation and severity. Removed
  `review_*`, assertion/private-import boolean flags, binary helper conventions,
  `ordering.enabled` and the experimental preset. Public-first ordering needs an
  explicit rule entry; options alone do not enable it. Package `rules` use the
  same names and closest-package precedence. Stable SMT codes, debt identities
  and architecture-policy activation are retained. See the complete
  [migration table and developer-facing YAML API](docs/configuration.md).
- `--select` and `--ignore` accept the same canonical names as YAML and `explain`.
  `context` reports effective rule severities for existing and planned modules.
  Scoped-off debt is retained as unassessed when recording/pruning.
- `smelt init --maintainability` writes explicit individual checks, plus a local
  `.smelt.schema.json` and YAML modeline. Editor completions, validation and hover
  examples come from the real catalog and work offline. Plain `init` keeps its
  conservative check selection. Existing configs can export `smelt config schema`
  and add the modeline; see [editor setup](docs/configuration.md#editor-completion-hover-and-validation).
- Optional [SMT430](docs/rules/VIS010.md), enabled by
  `rules: {test-only-public-attribute: warning}` or `--select SMT430`,
  reviews public instance/class data attributes read only by tests outside their
  class. Findings include test locations and a collision-free private name.
  Possible production accesses and uncertain class contracts preserve visibility;
  checks never rename code. Public-use rules now share the same member index,
  export/import evidence and private-name suggestions. The new rule is off by
  default and never renames code automatically.
- Optional [SMT318](docs/rules/MOD001.md), enabled with `rules: {SMT318: hint}`
  or `--select SMT318`, reviews direct `StrEnum.MEMBER.value` reads for using the
  string enum member directly. Known import aliases and analyzed re-exports are
  supported; ordinary enums, uncertain bindings and instance inference are
  excluded. Exact-string boundaries can still need a conversion, so findings
  remain advisory and checks stay read-only. The default rule set is unchanged.
- `smelt check --staged` reviews the actual Git index against HEAD, using staged
  source, configuration, Python metadata and debt. The new `smelt-staged` hook
  blocks new warnings as well as errors, including findings in unchanged methods
  whose last caller was removed. Partial staging and untracked files cannot hide
  commit findings. JSON/SARIF distinguish staged scope from working-tree changes.
- Content-addressed JSON caches reuse complete analyses, per-file member facts
  and the historical baseline. `--no-cache` recomputes independently. Member-use
  analysis avoids repeated per-function tree walks; cache invalidation includes
  contents, inventory, policy, Python targets and analyzer implementation. See
  [the commit workflow and benchmark](https://github.com/mathisarends/smelt/blob/main/docs/agent-workflow.md). Changed/staged
  snapshots reject symbolic links; staged fixes require editing and re-staging
  working files explicitly.
- SMT316 feedback explains how to retain a deliberate public API with a reasoned
  `# smelt: ignore[SMT316] -- ...` comment on its definition. Existing suppression
  reason and unused-suppression checks continue to apply with caching.

- Optional [SMT316](docs/rules/VIS001.md), enabled with
  `rules: {internal-public-member: warning}`, reviews public module functions and
  ordinary instance methods with no observed external use, including unused
  definitions. Findings suggest a private name and list internal uses/callbacks
  so agents can review renames. Existing private names are avoided. Explicit
  exports, decorators, inheritance and possible external method accesses preserve
  public APIs; test-only public module functions remain covered by SMT429.
- Optional [SMT317](docs/rules/LAY005.md), enabled with
  `rules: {internal-static-helper: hint}`, suggests extracting internal static helpers
  to private module functions. Class dependencies, inheritance, extra decorators
  and possible external callers preserve deliberate static APIs. Both new
  rules support closest package `off` overrides and appear in `smelt context`.
  Checks remain read-only and the default rule set is unchanged.
- Optional [SMT429](docs/rules/VIS009.md), enabled by
  `rules: {test-only-public-function: warning}`, reviews public module functions
  accessed from tests but not from other analyzed production modules. Findings
  suggest a leading underscore and include test locations so agents can review
  the rename and test the public caller. Explicit exports and decorated functions
  stay public; external/dynamic consumers remain a review limitation. The default
  rule set is unchanged.

## [0.1.6] - 2026-10-29

### Compatibility and activation

- SMT428 remains opt-in through `tests.review_async_mock_assertions`; the default
  rule set is unchanged. Its findings ask for review of an individual test, not a
  claim that the suite misses the regression.
- Existing `tests.review_test_deadlines` policies can now report additional known
  asyncio subprocess waits. Review each operation's local deadline, including
  cleanup paths, rather than adding a blanket timeout or await restriction.

### Added and fixed

- SMT426 now reviews `wait()` and `communicate()` on known asyncio subprocesses
  created through awaited standard factories or explicit Process annotations.
  Each operation needs its own local guard, including exit waits after bounded
  reads or `kill()`. Unknown process-like objects remain outside scope.
- Optional SMT428 (`tests.review_async_mock_assertions`) reviews call-only checks
  on known async mocks and async class-spec methods. Await assertions, deliberate
  scheduling checks and synchronous methods remain valid. Known fixture specs and
  simple local history values are traced without executing analyzed code; unknown
  methods and general data flow remain outside scope. Executed missing-await
  counterexamples demonstrate the distinction between call and await assertions.
- `context` text now shows all adopted test-quality review flags, including when
  only the newer policies are enabled.
- SMT424 recognizes awaited, typed Event observations inside local timeout
  contexts that raise on expiry, consistently with SMT426 deadline recognition.
  Unused contexts, unawaited waits and `move_on_after` scopes are not outcome
  evidence; configured timeout helpers alone still do not count as assertions.

## [0.1.5] - 2026-10-29

### Compatibility and activation

- Existing opted-in test policies can report additional MonkeyPatch operations
  and known instance accesses. New SMT425-SMT427 review policies remain opt-in;
  the small default rule set is unchanged. Review legitimate boundary tests
  rather than exposing internals or imposing a global mock ban.
- `--fail-on warning` now includes configuration warnings, and unused
  `pytest.raises`/`warns` context objects no longer count as outcome evidence.
  A passing static report is not proof of test effectiveness.

### Added and fixed

- `scripts/evaluate.py` supports repeatable agent review loops: compare two import
  roots on identical config bytes, retain
  structured recognition changes and independently bounded logs, then optionally
  execute explicitly selected pytest targets. Invalid analysis, timeouts and
  incomplete collection remain distinct from test failures; normal checks never
  execute analyzed tests. See the [evaluation workflow](docs/testing.md#evaluate-a-test-quality-change).
- Test outcome review now recognizes known mock interaction assertions, annotated
  fixture receivers and awaited, bounded asyncio Event observations. Unused
  `pytest.raises`/`warns` context objects no longer count as checks. Unknown
  `assert_*` methods and fixture execution alone remain insufficient evidence.
- [SMT420](docs/rules/TST002.md) tracks direct known production constructors and
  explicitly typed instance reads, including analyzed facade re-exports and
  consistent known fixture return/yield tuple components. Unknown/conflicting
  outputs, private writes and local fakes are excluded; fixture inference has
  separate provenance and respects class/conftest scope. A private class import
  is not reported again for each public instance operation.
  [SMT421–SMT423](docs/rules/TST004.md) now include MonkeyPatch attribute
  replacement, aliases and context scopes, with operation/provenance metadata.
  MonkeyPatch environment/mapping setup is excluded from the mock count.
- Optional [SMT425](docs/rules/TST007.md), [SMT426](docs/rules/TST008.md) and
  [SMT427](docs/rules/TST009.md) review potentially vacuous outcomes, known waits
  without deadlines/scheduler-clock replacements, and small independent-case loop
  candidates. They remain advisory and require explicit test policies; no fixer,
  blanket mock/sleep ban or assertion quota is introduced.
- Configuration warnings now appear in JSON `configuration_diagnostics`, SARIF
  configuration notifications, GitHub annotations and text summaries. They count
  toward `summary.warnings` and `--fail-on warning`, including scoped/changed
  checks; rule selection, suppressions and debt cannot hide configuration warnings.

## [0.1.4] - 2026-10-29

### Migration: rule activation changed

- Only **SMT301, SMT901, SMT902 and SMT903** run by default. Declaring an
  architecture, package or test policy activates the corresponding checks without
  a second enable switch. The catalog now distinguishes 4 default, 24 policy-driven
  and 2 explicitly selected rules; activation and severity are separate.
- **SMT104 import cycles and SMT305 unclassified-module hints require explicit
  selection.** To retain these checks from earlier versions, add:

  ```yaml
  rules:
    SMT104: error
    SMT305: warning
  ```

- `--select SMT3` and `--select ALL` activate matching opt-in checks and replace
  normal selection. Explicit `off` settings and ignores still win; selection does
  not invent missing policies. Strict coverage remains mandatory when configured
  or requested, even outside the selected rule prefix.
- `smelt init` still proposes architecture but leaves naming conventions and test
  mirroring disabled for review. A detected mirror pattern remains available as a
  setting, with an activation comment.
- Debt for inactive or hint-only rules is retained when recording/pruning, rather
  than silently considered resolved. Older cycle and indirect-import debt can
  resurface because corrected fingerprints track the actual dependency. Review
  these findings before recording new debt.

### Added

| Capability | Rules / interface | What agents can now check |
| --- | --- | --- |
| Strict architecture coverage | [SMT306](docs/rules/ARC013.md), `project.coverage: strict`, `--strict-coverage` | Production files outside package selection, missing architecture roles and exclusions without reasons. These gaps cannot be hidden by changed/path filters, suppressions or debt. |
| Package structure and public APIs | [SMT107](docs/rules/ARC007.md), [SMT307](docs/rules/ARC014.md) | Required modules and explicit public entry points through `structure.contracts`; outside callers cannot import private implementation modules. |
| Import fanout | [SMT108](docs/rules/IMP001.md) | Optional hints when one outside caller imports too many distinct modules from the same contracted package. |
| Source conventions | [SMT308](docs/rules/IMP003.md)–[SMT312](docs/rules/LAY003.md) | Scoped absolute/relative import styles, module-docstring policy, future-annotation policy and private module/package names. Python targets come from declared metadata or configuration. |
| Definition order | [SMT313](docs/rules/LAY004.md) | Dependency-aware module/class ordering, including execution barriers and bounded, explicitly requested fixes. |
| Instance field assignments | [SMT314](docs/rules/IDM001.md) | Bundled or chained writes to multiple direct instance attributes when `instance_assignments: separate` is declared. |
| Type dispatch | [SMT315](docs/rules/IDM002.md) | Review hints for same-subject `isinstance`/`elif` chains under a declared `type_dispatch` policy. |
| Test quality | [SMT420](docs/rules/TST002.md)–[SMT424](docs/rules/TST006.md) | Configured private production access, forbidden mocks, string patch targets, per-test patch limits and missing recognized outcome assertions. |

- `smelt context <path> --format json` explains ownership, coverage status,
  applicable contracts, effective conventions and Python targets for existing or
  planned files. Planned placement is distinguished from analyzed source.
- `check --show-coverage` exposes the per-file policy inventory. JSON reports add
  discovery roots, classification counts, policy references, exclusions and a
  policy hash. Rule metadata exposes activation, analysis requirements and
  configuration paths through `rules`, `explain` and reports.
- Contributor documentation and catalog checks verify explicit registration,
  unique rule codes/names, complete metadata and generated documentation.
- Tag-triggered PyPI Trusted Publishing validates the tag/version and main
  ancestry, runs checks, builds and smoke-tests both distributions, then publishes
  the validated artifacts without a stored PyPI API token.

### Fixed

- Resolve named re-exports across layer, feature, shared and composition-root
  boundaries. Indirect findings retain the final forbidden target; declared
  feature wiring no longer produces false unknown-layer findings.
- Track every cyclic architecture edge and semantic identities/multiplicity in
  changed/debt comparisons. New edges, patches or ordering inversions cannot hide
  behind an accepted aggregate finding. Existing violations do not become new
  merely because an import's formatting, comment or alias changed.
- Merge namespace package portions, reject duplicate module definitions and infer
  usable workspace source roots. Grouped imports check every alias, and source
  discovery avoids symlink cycles.
- Reject duplicate YAML keys and invalid source encoding. With `--format json`,
  usage/configuration/analysis errors return structured error documents; reports
  identify their origin and comparison scope. SARIF and GitHub paths are rebased
  correctly for nested configurations.

### Agent workflow and limits

```bash
smelt rules --format json
smelt context path/to/module.py --format json
smelt check --changed --base origin/main --format json
smelt check --strict-coverage --show-coverage --format json
smelt explain SMT313 --format json
```

- Normal checks are read-only. After configuring ordering with `fix: safe-only`,
  `smelt check --select SMT313 --fix` can apply bounded definition moves. It
  preserves source trivia, validates the permitted AST moves and refuses unsafe
  or concurrently changed files. Run the project formatter afterwards.
- SMT314 and SMT315 have no automatic rewrite. Splitting assignments or converting
  type dispatch can change evaluation, guards and fallthrough.
- Coverage means policy assignment within declared discovery roots, not a proof
  of behavior. Package APIs and test-quality checks are static and do not infer
  dynamic imports, arbitrary runtime access or fixture dependency effects.
- Import fanout, match suggestions and missing-assertion checks are review aids;
  their default severities differ. Configure policy and severity deliberately.

[Unreleased]: https://github.com/mathisarends/smelt/compare/v0.16.1...HEAD
[0.16.1]: https://github.com/mathisarends/smelt/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/mathisarends/smelt/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/mathisarends/smelt/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/mathisarends/smelt/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/mathisarends/smelt/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/mathisarends/smelt/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/mathisarends/smelt/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/mathisarends/smelt/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/mathisarends/smelt/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/mathisarends/smelt/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/mathisarends/smelt/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/mathisarends/smelt/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/mathisarends/smelt/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/mathisarends/smelt/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/mathisarends/smelt/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mathisarends/smelt/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mathisarends/smelt/compare/v0.1.6...v0.2.0
[0.1.6]: https://github.com/mathisarends/smelt/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/mathisarends/smelt/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/mathisarends/smelt/compare/v0.1.3...v0.1.4
