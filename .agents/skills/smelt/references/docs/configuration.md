# Configuration and inheritance

Use `smelt.yml` for project configuration. `smelt init` creates it by default.
`smelt.yaml` is also supported. With both names in one directory, automatic
discovery picks `smelt.yml`.

| Key | Root | `extends` base | uv member | How policy resolves |
| --- | --- | --- | --- | --- |
| `version` | Required after composition | Optional | Optional | The effective root uses `version: 2`. |
| `extends` | One local file | One local file | ? | Bases apply first. The path is relative to the file declaring it. |
| `project` | Roots, package selection, workspace, coverage, exclusions | Optional defaults | `exclude` only | Root source/test roots apply inside each member. Member exclusions add to the root exclusions. |
| `rules` | Shared rule settings | Optional layer | Member settings | Base, root, member, then closest applicable `style.overrides` entry. A member needs a reason to lower inherited severity. |
| `style` | Shared source conventions | Optional defaults | Member conventions | A member inherits root values option by option. Changing an inherited value requires a reason. |
| `style.overrides` | Root-owned packages | Optional entries | Member-owned packages | The closest matching package that sets an option wins. |
| `style.forbidden_names`, `style.file_naming` | Workspace-wide | Optional defaults | ? | Apply from the root throughout the workspace. |
| `architecture.features`, `layers`, `modules`, `shared`, `composition_root`, `wiring`, `cross_feature` | Root-owned packages | Optional defaults for the root | Member-owned packages | Each project declares its own local layout. |
| `architecture.packages` | Package APIs of root-owned packages | Optional entries for the root | Package APIs of member-owned packages | Each applicable package API constrains its callers. |
| `architecture.members`, `architecture.import_graph`, `architecture.canonical_exports` | Workspace-wide | Optional defaults | ? | The root governs member boundaries, graph options and canonical exports. |
| `tests`, `suppressions` | Workspace-wide | Optional defaults | ? | Apply from the root throughout the workspace. |
| `ignore`, `debt` | Shared ignores and fallback debt | Optional defaults | Member-relative paths | Member ignores add to root ignores. A member debt file takes precedence for that member. |

In the `extends` chain, mappings usually merge and lists replace. Each `rules`
map is a layer. A reasoned style option replaces the entire inherited option,
including any previous reason. Workspace members inherit root policy directly.
The root and each member own their internal architecture.

Configure each fixed check by its individual name or code. Rule names have
the form `family/finding`. The value activates that check and sets its severity.
A family selector can disable or cap rules that are already active.
See [families](rule-selection.md#families).
The same names appear in YAML, CLI selection, `explain`, diagnostics and JSON.
Each code starts with its family prefix, such as `ARC001` or `TST004`.
Old `SMTnnn` codes are rejected with the new code; see the
[upgrade guide](upgrading.md#use-the-family-rule-codes).

## A small Python project

For a project containing `src/harnessed/` and `tests/`:

```yaml
# yaml-language-server: $schema=.smelt.schema.json
version: 2

project:
  root_packages: [harnessed]
  source_roots: [src]
  test_roots: [tests]

rules:
  layout/definition-order: error
  idioms/bundled-instance-assignment: error
  visibility/internal-public-member: warning
  visibility/internal-public-module-symbol: warning
  visibility/internal-public-attribute: warning
  visibility/test-only-public-function: warning
  visibility/test-only-public-attribute: warning
  layout/misplaced-static-helper: hint
  tests/missing-outcome-check: warning
  tests/vacuous-assertion: warning
  tests/unbounded-wait: warning
  tests/independent-case-loop: hint
  tests/missing-await-assertion: warning
```

Generate this explicit starting selection with `smelt init --maintainability`.
The flag chooses what `init` writes; later checks read the configured entries.
The visibility reviews cover APIs used only by tests. See
[visibility reviews](rule-selection.md#information-hiding).
Plain `init` retains its conservative architecture starter. Neither command
overwrites an existing config without `--force`.

For a flat package, use `source_roots: ['.']`. Root packages can be omitted and
discovered from the source roots. Namespace packages and uv workspace roots are
supported; loose standalone scripts are outside package selection. Source rules
also work without tests. Test roots name the tests that provide review evidence.

Larger projects use the same rule map and add their `architecture` policies.
These conventions do not assign architecture roles: `--strict-coverage` separately
requires every discovered production source to have an architecture policy or a
reasoned exclusion.

## Shared local policy

Independent projects can share explicit policy through one local `extends`
file. Each project still has its own configuration and `--config` invocation.
For one policy across a uv workspace, see [uv workspaces](#uv-workspaces)
instead:

```yaml
# smelt.base.yml (a policy fragment; no project selection is required here)
version: 2
rules:
  architecture/import-cycle: error
  tests/unbounded-wait: warning
suppressions:
  require_reason: true
```

```yaml
# runtime/smelt.yml
extends: ../smelt.base.yml
project:
  source_roots: [src]
  test_roots: [tests]
architecture:
  features: {root: runtime_extension.features}
rules:
  architecture/uncovered-source: error
  tests/unbounded-wait: error
```

Run `smelt check --config runtime/smelt.yml` from the repository root, or
`smelt check` from `runtime/`; both select the same project and paths.

- `extends` is one nonempty relative local file path, resolved from the file
  containing it. A base
  may extend another base, up to 32 files. Missing files,
  inheritance cycles, invalid YAML and duplicate YAML keys are configuration errors.
  There is no remote loading, multiple inheritance or automatic cascading discovery.
- Mappings merge recursively. Explicit child values win. Lists replace the entire
  inherited list, including `[]`; allowances, exclusions and ignores never append
  silently. An empty mapping leaves inherited keys in place. Scalars and `null`
  replace inherited values; the result must satisfy the field's type.
- Defaults, package discovery and cross-field validation run on the complete
  effective configuration. A fragment need not declare `project` or `version`,
  but the combined result must declare `version: 2` and `project`. The editor
  schema permits fragments; Smelt checks required fields after composition.
- **All project paths use the selected project's directory**, including inherited
  source/test roots, project exclusions, test globs, `ignore.paths` and `debt`.
  For example, inherited `source_roots: [src]` means `runtime/src` above, even if
  written in the repository-level base. Only the `extends` reference itself uses
  its declaring file as the anchor. Dotted module patterns are unchanged.
- `rules` do not merge key by key: every file is its own rules layer, and a
  named setting can replace an earlier setting. Families and `ALL` can
  cap or disable active rules. The base may spell a rule by code and the child by name.
  Within one file, a rule name and its code together remain a
  duplicate-alias error. Unknown rules, invalid severities and strict-coverage
  restrictions still fail.
- An inline style setting with `{value, reason}` replaces the whole option.
  Update the reason when replacing the option in a later file.

`smelt config show` prints the resolved root policy. Add `--sources` to see
`config`, `sources` (bases first, then the root and member files), `origins`
(explicit YAML key locations and their declaring files) and `rule_selectors`
(matches for broad selectors). A workspace adds `workspace_members` containing
the settings from each member file. Use `--format json` for structured output.
Defaults and discovered package names have no declared origin. Use
`smelt context PATH` to see which values, rules and reasons govern a particular
module, including a planned path.

Every check reloads policy inputs before analysis reuse. Staged checks read every
base from the captured Git index, never from unstaged working files; bases must
therefore be staged and inside the repository. Editing a base invalidates dependent
staged results, including when the project YAML itself is unchanged. `--changed`
and `--staged` continue comparing old and new source under the same effective
current/index policy. Run a full check to inventory findings after changing policy.
An explicit check path naming any loaded base widens that project's check to
the whole project. It does not automatically run other project configurations.
The maintainer evaluation runner records every policy input's SHA256 in
`config_inputs` and rejects a comparison if a base changes during evaluation.

## uv workspaces

A uv workspace is checked as one project: one analysis, one import graph and one
report. Cross-member usage therefore counts, so a function in `packages/core`
that only `services/api` calls is not reported as unused. The root config
declares the workspace and the shared policy:

```yaml
# smelt.yml (workspace root, next to the pyproject.toml with tool.uv.workspace)
version: 2
project:
  workspace: uv
  test_roots: [tests]
style:
  future_annotations: when-needed
rules:
  complexity/excess-positional-parameters: {severity: warning, max: 3}
  tests/missing-outcome-check: warning
architecture:
  members:
    "services/*": {may_depend_on: ["packages/*"]}
    "packages/*": {may_depend_on: []}
```

- `tool.uv.workspace.members` and `exclude` in `pyproject.toml` select the
  members. Smelt does not keep a second list.
- Root `source_roots` and `test_roots` apply inside every member. A member
  contributes those that exist, for example `services/api/src` and
  `services/api/tests`. Without `source_roots`, each member uses `src/` if it
  holds a package and otherwise the member directory itself. Test roots at the
  workspace root, such as end-to-end tests in `tests/`, are included too. Root
  source roots count only when the root `pyproject.toml` has a `[project]`
  table, because uv then treats the root as a member.
- Top-level package names must be unique across members. `root_packages`
  defaults to every member's packages; an explicit list narrows them.

Keep each member's architecture and exceptions in its own `smelt.yml`. A member
without this file still inherits the shared root settings.

```yaml
# services/api/smelt.yml
rules:
  typing/redundant-future-annotations: {severity: hint, reason: Review legacy forward references first.}
  visibility/internal-public-member: error
  complexity/excess-positional-parameters: {max: 2}
architecture:
  packages:
    http: {match: api.http, public: ['.']}
ignore:
  - {rule: tests/excess-patches, paths: [tests/legacy/**], reason: Migrating.}
project:
  exclude: [{path: src/api/generated/**, reason: Generated client.}]
```

- **Allowed keys:** `version`, `project.exclude`, `style`, `rules`, `debt`,
  `ignore` and `architecture`. The graph policy, member boundaries and canonical
  exports are configured at the root. A member cannot
  set `extends`, `tests`, `suppressions` or other `project` keys. Smelt
  reports each of these as an error that names the member file.
- **Paths are member-relative.** `ignore.paths` and `project.exclude` paths are
  resolved from the member directory.
- **`rules` apply by path** to the member's sources and tests. The root's
  setting comes first, then the member's, then the closest style override.
  See [rule precedence](rule-selection.md#workspace-members).
- **`style` options apply to the member's source packages.** A more specific
  `style.overrides` entry lives in the same member file.
- **Package API and style matches stay with their owner.** Write
  `architecture.packages` and `style.overrides` in the config of the project
  that owns the matching packages. Anchor member patterns to an owned top-level
  package, such as `api.*`.

`smelt check` from a member directory, or with `--config services/api/smelt.yml`,
loads the workspace root and checks only that member. Run `smelt check` at the
workspace root to include every member. A `smelt.yml` or `smelt.yaml` in a
subdirectory of a member or in an undeclared directory is ignored with a
configuration warning. In strict coverage mode, a member cannot lower ARC013.

### Member architecture

A member's `architecture` describes its own features, layers and boundaries:

```yaml
# services/api/smelt.yml
architecture:
  features: {root: api.features}
  shared: [api.common]
  layers:
    domain: {path: domain, third_party: deny}
    application: {path: application, may_depend_on: [domain]}
```

- Root layers and features govern packages owned by the root project. A member
  declares its own architecture, including when it shares a layer layout with
  another member. An empty or omitted architecture leaves the member without local
  layers. Strict coverage still checks its sources.
- Every module it names (`features`, `shared`, `composition_root`, `modules`,
  `wiring`) must lie inside the member's own packages. Layers without features
  span the member's packages, not the whole workspace.
- Local layer, feature, shared and wiring rules judge dependencies between
  packages of the same owner. Third-party policies apply to imports by that
  owner's layers. Between owners, use `architecture.members` for dependency
  allowlists. Package APIs and import graph checks can use the workspace graph.
- `architecture.import_graph` (which imports count) and `architecture.members`
  (boundaries between members) are set once in the workspace root.
- `smelt context` for a member feature or path shows the member's layers. In
  strict coverage mode, ARC013 and ARC012 use the architecture that governs each
  module, and `coverage.member_architectures` lists the packages each member
  architecture governs.

Test options remain in the root. A member can declare its own member-relative
`debt` file; see [workspace debt](adoption.md#workspace-debt).

### Reasoned member exceptions

Keep shared style in the root, internal architecture in each member, and
boundaries between members in root `architecture.members`. A member may enable a
rule or raise its severity with a short value. Lowering an inherited severity,
including setting `off`, requires an object with a nonempty `reason`:

```yaml
# packages/legacy/smelt.yml
rules:
  complexity:
    severity: warning
    reason: Migrating legacy constructors before enforcing the root error level.
```

The guard compares effective selectors with the root policy, including rule
activation and shared root style. It applies to member `rules`
and rules in member style overrides. `on` resolves to each rule's catalog
severity before comparison. A more specific entry must carry its own reason if
it weakens the policy. The reason of an overridden family does not carry over.
Empty or whitespace-only reasons are config errors. Strict coverage
cannot be lowered even with a reason. Root rules and style overrides may also use the
object form, but only member weakening requires it. Reasons survive
`config show --sources`; finding severities remain plain values in reports.

A member that changes an inherited source convention must use a nonempty inline
reason beside `value`. This applies to scalar, list and mapping-valued
options. A rule's `max` in member `rules` refines the limit for the member and
needs no reason. Member `style` inherits root conventions, and `style.overrides` inherits
any matching member value before falling back to the root. A matching value or
an option left open by the surrounding policy needs no reason.
Root style also accepts the inline form. Existing `reasons` maps can still
be read. `smelt context` shows the effective value, source and reason.

```yaml
# packages/legacy/smelt.yml
style:
  future_annotations:
    value: allow
    reason: The legacy API still exports runtime-evaluated annotations.
  overrides:
    generated:
      match: legacy.generated
      dataclass_options:
        value: []
        reason: The generator owns dataclass decorators in this package.
```

`style.forbidden_names`, `style.file_naming` and
`architecture.canonical_exports` are root-only.

### Workspace initialization

Run `smelt init` in a uv workspace root to write a root config and a config in
each discovered Python member. `--maintainability` adds named `rules` entries
to the root. Smelt drafts a local layout when it can infer layers, features,
shared code or a composition root. The commented `architecture.members`
example uses the workspace's member globs and pre-fills the observed target
members. Review the allowed targets before enabling it.
Keep packaging declarations in `pyproject.toml`; ARC010 checks those separately.

Root source roots are omitted so each current or future member uses its own
`src/` directory or flat package layout automatically. Test roots cover `tests`
and `test` inside every member. Non-Python and excluded members are skipped.
Drafts need review before adopting strict coverage or recording debt.

Use `smelt init --member packages/new` to draft just one declared Python member
under an existing `project.workspace: uv` root. The path is workspace-relative.
The root config and other member configs are preserved. Existing `.yml`/`.yaml`
filenames are respected. Without `--force`, any existing target refuses the
entire write before the root config, member configs or editor schema change.
With `--force`, ordinary workspace init overwrites all generated config targets;
with `--member`, only that member's target is overwritten. Editor schemas are
replaced only when identified as Smelt-generated.

### Boundaries between members

Keep internal layers in each member and declare dependencies between members in
the root `smelt.yml`:

```yaml
architecture:
  members:
    "services/*": {may_depend_on: ["packages/*"]}
    "packages/*": {may_depend_on: []}
```

This enables [ARC009](rules/ARC009.md) `architecture/member-boundary`.
The keys and allowances are workspace-relative path globs, using `*`, `**` and
`?`; `.` names the root project. Every key and every allowance must match at
least one analyzed member (or `.`); a glob that matches nothing, such as a typo,
is a configuration error rather than a boundary that silently checks nothing.
A source member matching no key is unrestricted.
If several keys match, their allowances combine. Imports inside the same member
always pass. Member-local wiring or shared roles do not bypass these boundaries.
Only direct production imports count, regardless of `architecture.import_graph.transitive`;
`TYPE_CHECKING` imports follow the root `architecture.import_graph.type_checking` setting.

`project.workspace: uv` also enables [ARC010](rules/ARC010.md)
`architecture/undeclared-member-dependency`. A production import of another member
requires a declaration in the importing member's `project.dependencies` or
`project.optional-dependencies`. If the root's member boundary forbids the import,
ARC010 advises removing or moving it. Distribution names come from `[project].name`,
independently of Python package names; for example package `core` can belong to
distribution `core-lib`:

```toml
# services/api/pyproject.toml
[project]
name = "web-api"
dependencies = ["core-lib"]

[tool.uv.sources]
core-lib = {workspace = true}
```

An entry in `tool.uv.sources` alone does not declare a dependency. Sources in the
root apply to all members, and a member's source for a dependency replaces the
root's entry. Workspace sources and local paths must identify the imported
member; an explicit source elsewhere does not supply it. Local source paths are
relative to the file declaring them. Without a source entry, a matching
distribution name counts as declared; Smelt does not validate uv's resolver setup.
Names are normalized, and all marker/extra branches count without evaluating the
analyzer's environment. Development groups and test imports are outside ARC010.
Members with dynamic dependencies or missing project metadata are skipped rather
than guessed. Invalid static requirements or unreadable metadata are analysis errors.
Only a direct import requires a direct declaration.

Both rules use the existing severity, ignore, suppression and debt mechanisms.
They run on the shared import graph even when members declare different internal
architectures. JSON findings add `member`: the member directory, `.` for workspace
root files, or `null` outside a workspace or without a file location.
`smelt check --statistics --by member` counts findings per member, including hints;
the default grouping remains per rule. `--show-coverage` includes
`coverage.member_boundaries`, the effective root allowlists.

## Options and package exceptions

### Where an exception belongs

Choose the narrowest mechanism that states why the code differs:

| Mechanism | Use it for | Scope |
| --- | --- | --- |
| `project.exclude` | Code that should not be analyzed at all, such as generated clients | Path globs, optional reason |
| `ignore` | Findings of a rule that do not apply to some files or modules | Path globs or modules, any rule, required reason |
| `style.overrides` | Different conventions, severities or limits for a package | Package `match`; source rules and style options |
| `# smelt: ignore[CODE] -- reason` | One deliberate exception at one location | A single finding |
| [Debt](adoption.md) | Accepted existing findings you plan to fix | Recorded findings; new ones still fail |

`ignore` and `style.overrides` are separate on purpose. `ignore` hides
findings and always needs a reason. `style.overrides` changes the policy that
applies to a package and can also enable a rule there. Use `ignore` for test
files; package overrides cover only production modules.

API ownership can be stated separately from export-frequency review:

```yaml
architecture:
  canonical_exports:
    app.error.types.FeatureError: app.FeatureError
  packages:
    errors:
      match: app.error
      min_importers_to_export: 3
```

Both names must resolve to the same analyzed symbol through bounded static
re-export chains. The target must actually be public, including `__all__` when
declared. Missing, conflicting and unverifiable ownership is a config error,
even when ARC008 is not selected. Without an ownership declaration, existing
ARC008 behavior is retained. Declaring one owner prevents duplicate facade advice
for that symbol; it does not make other internal aliases public or exempt imports
from layers, package APIs or cycle checks. No callers or exports are
rewritten. Review the existing export paths before adding another API declaration.

Cycle analysis can separately select exact module edges and annotation participation:

```yaml
architecture:
  import_graph:
    cycles: [modules]
    type_checking: include
    type_checking_cycles: ignore
```

This retains annotation imports in dependency-boundary checks while excluding
them from cycle graphs. Defaults remain `cycles: [features, layers, siblings]`
and `type_checking_cycles: include`; `include` respects the global `type_checking`
setting. Existing scopes aggregate architecture nodes and can form cycles without
a connected concrete module cycle. Runtime witness
edges mean outside `TYPE_CHECKING`, including deferred imports, and do not prove
eager import-time failure.

Fixed patterns need no additional policy. Real parameters stay with their concept:

```yaml
style:
  overrides:
    ordering-options:
      match: harnessed
      ordering:
        constants_exempt: true
        fix: off
    supported-integrations:
      match: harnessed.integrations
      rules:
        visibility: off
        layout/misplaced-static-helper: off
    strict-core:
      match: harnessed.core
      rules:
        visibility/internal-public-member: error

tests:
  assertion_helpers: [tests.assertions.assert_response]
  timeout_helpers: [tests.timing.deadline]
  quality_exclude: ['tests/generated/**']
```

`ordering` contains options, not activation. Enable `layout/definition-order` in
`rules`; public must precede private in any customized module/class category list.
Dependencies and execution barriers still take precedence over category order.
`fix: off` disables ordering writes; plain checks are always read-only.

Package `rules` use the same selectors and values as the project map. They apply
to findings originating in production modules of the matching package, including
test-only API findings located in production code. The closest enclosing package
entry wins for each rule. It can change severity, disable a global check, or
enable it only in that package. Conflicting settings for the same concrete package
and duplicate name/code entries are config errors. Package rule settings cannot
disable strict coverage. Test-file and project rules are rejected in package
overrides; use reasoned `ignore.paths` for exceptions in test files. Rule metadata
exposes `scope` as `source`, `test` or `project`, and package completions show
only applicable source rules.

Architecture/interface policies and parameterized conventions such as import
style, required modules, mirror layout, forbidden mock paths and patch limits
describe what their rule checks. Only a `rules` entry enables the rule. A
declared policy whose rule no entry names is a configuration warning; enabling
a rule whose required policy is missing is a configuration error. See
[rule activation](rule-selection.md#activation-and-precedence).

Source-convention options such as `future_annotations` or
`dataclass_options` can be set directly under `style` for the whole project and
overridden in `style.overrides`. Enable the matching rule in `rules`, or in the
override's `rules` to check only those packages; see
[project-wide options](structure.md#project-wide-options-and-package-overrides).

A rule that counts something takes its limit as `max` in its own entry, for
example `complexity/excess-positional-parameters: {severity: warning, max: 3}`.
An entry in a style override's or member's `rules` may set only `max`; it keeps
the inherited severity and refines the limit for those files. Families and
`ALL` take no `max`. See [limits](structure.md#limits).

```bash
smelt check --select visibility/internal-public-member,tests/unbounded-wait
smelt check --select LAY --ignore layout/misplaced-static-helper
smelt explain tests/missing-await-assertion
smelt rules --format json
smelt config show
smelt context harnessed.core --format json
```

`--select` replaces normal selection. `off` settings and CLI ignores apply; a
closer package entry can deliberately re-enable a globally disabled rule.
`--ignore` excludes the rule across the run. Strict coverage remains mandatory.
`context` reports effective rule severities even for planned source files.
Turning a check off in a package does not report its old debt as fixed.

## Editor completion, hover and validation

`smelt init` writes `.smelt.schema.json` beside the config and adds the modeline
shown above. The schema is generated from the installed version's real rule
catalog. Hover content includes the precise pattern, a bad/good example, repair
advice and analysis limits. Completions show canonical problem names and allowed
severities; unknown names, old switches and invalid values are flagged.

The schema artifact has no effect on checking or activation. You can commit it
with the YAML for shared offline editing support, or ignore `.smelt.schema.json`
and regenerate it locally to keep generated hover content out of the repository.
After updating Smelt, refresh it from the config directory:

```bash
smelt config schema > .smelt.schema.json
```

For an existing YAML, export that file and add the modeline manually. Paths in the
modeline are relative to the config file. `init` refreshes its own generated schema;
it refuses to overwrite an unrelated file at that path. A custom `--config`
gets a schema beside that config, leaving reviewed configs elsewhere alone.

- **VS Code / Cursor:** install Red Hat's YAML extension. The modeline associates
  the schema with the file. Other YAML Language Server clients can use the same
  comment. See the [language server's schema association documentation](https://github.com/redhat-developer/yaml-language-server#associating-schemas).
- **PyCharm / IntelliJ:** select the local `.smelt.schema.json` using the schema
  switcher or **Languages & Frameworks → Schemas and DTDs → JSON Schema Mappings**.
  See [YAML schemas in PyCharm](https://www.jetbrains.com/help/pycharm/yaml.html#json_schema).

The exported schema documents configuration shape. File existence, package
matches, aliases and architecture semantics are still validated by Smelt.

## Breaking-change migration

This section covers the 0.1.x to 0.2.0 configuration migration. For upgrading to
0.3.0, including JSON reports and installation pins, see [Upgrading Smelt](upgrading.md).

Removed switches are errors, not silent aliases. Move fixed-check activation to
the rule map; use `off` instead of a former `false`/`allow` exception. Existing
numeric codes keep their meanings. That migration kept `version: 1`; 0.14 moved
the format to `version: 2` (see [Upgrading Smelt](upgrading.md#013-to-014)).

| Removed setting | Explicit rule |
| --- | --- |
| `ordering.enabled` or an implicitly activating `ordering` object | `layout/definition-order` (LAY004) |
| `instance_assignments: separate` | `idioms/bundled-instance-assignment` (IDM001) |
| `member_visibility: private-when-internal` | `visibility/internal-public-member` (VIS001) |
| `static_methods: module-functions` | `layout/misplaced-static-helper` (LAY005) |
| `tests.forbid_private_imports` | `tests/private-access` (TST002) |
| `tests.forbid_string_patch_targets` | `tests/string-patch-target` (TST004) |
| `tests.require_assertions` | `tests/missing-outcome-check` (TST006) |
| `tests.require_non_vacuous_assertions` | `tests/vacuous-assertion` (TST007) |
| `tests.review_test_deadlines` | `tests/unbounded-wait` (TST008) |
| `tests.review_case_loops` | `tests/independent-case-loop` (TST009) |
| `tests.review_async_mock_assertions` | `tests/missing-await-assertion` (TST010) |
| `tests.review_test_only_public_functions` | `visibility/test-only-public-function` (VIS009) |
| `tests.review_test_only_public_attributes` | `visibility/test-only-public-attribute` (VIS010) |

Former names `definition-order`, `prefer-private-module-function`,
`missing-test-assertion`, `vacuous-test-outcome`, `test-deadline`,
`independent-test-cases` and `async-mock-call-only` are replaced by the canonical
names above, consistently in rule configuration, `explain`, findings and reports.
The experimental `preset` field and `init --preset` are removed. Public Python rule
classes also use these concepts, such as `PublicDefinitionsFirst`,
`TestWithoutOutcomeCheck` and `AsyncMockWithoutAwaitCheck`.
The enriched Python schema entry point is
`smelt.engine.schema.config_json_schema()`; the config-model layer no longer
exports a catalog-aware schema function.

Visibility and test-quality checks remain static review aids. A public helper
without observed external use can still be a supported external API. Preserve
intentional exports and review callers before renaming; Smelt does not rename
APIs or extract helpers automatically. A clean check does not prove test quality.
