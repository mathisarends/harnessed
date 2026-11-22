---
name: smelt
description: Configure and use Smelt to check Python architecture, package APIs, source conventions and test quality. Use for smelt.yml policies, Smelt findings, changed or staged checks, debt baselines and Smelt hook/CI integration.
---

# Use Smelt

Smelt is a static Python guardrail tool. Follow the project's existing policy and
use findings as evidence for a repair; advisory findings still require judgment.
This official skill and its offline manual ship with `smelt-cli`. `VERSION` in an
installed skill records the CLI version that installed it.

## Work in the actual project

1. Read project instructions, dependency pins and `smelt.yml` (or `smelt.yaml`).
   Use the project's command runner: examples below use `smelt`; a project-local
   dependency commonly uses `uv run smelt`. Check `smelt --version` against
   `VERSION`. After upgrading Smelt, refresh with `smelt skill install --force`
   using the same `--destination` if installed outside the default directory.
2. Before creating or moving code, run `smelt context <feature-or-module-or-path>`.
   Before writing a file, run `smelt context <path> --guide` and follow its
   instructions: one per active rule, rendered from `smelt.yml`, including the
   rules for the mirrored test file. Hints only apply where their case occurs.
   For architecture placement, inspect `layer_assignment` in `--format json`.
   A matching parent already covers the planned file. For a deliberate layer
   change in central code, add the narrowest qualified prefix to the intended
   layer's `modules` list.
   For inherited policy, inspect `smelt config show --sources --format json`.
   A planned path is valid for `context`; explicit `check` paths must already exist.
3. After editing, run `smelt check --changed --fail-on warning --format concise`
   (one line per finding; a repair shared by several findings is listed once
   below them). Use `--format text` for evidence and import chains, `--format json`
   only when a tool parses the report. Use `smelt explain <code-or-name>` and fix the underlying
   behavior or dependency. Run relevant tests and the project's other checks too.
4. For branch review use `--changed --base origin/main` (or the actual base).
   For the commit use `smelt check --staged --fail-on warning`; this reads the Git
   index, including partially staged files and policy, rather than the working tree.
   Run a full `smelt check --fail-on warning` after policy changes and for CI.

## Policy and repair rules

- Enable rules individually by name or stable code. A named `on` uses the
  catalog severity. Families and `ALL` in YAML can only disable or cap
  active rules. Use local `extends` files to share explicit policy.
  Policies never enable rules: name each rule that checks a declared policy.
  An unnamed policy is a configuration warning; enabling a rule without its
  required policy is a configuration error.
  Use `smelt rules --unconfigured` to find named checks to review after upgrades.
- `--select` replaces normal selection and accepts names, families,
  code prefixes and `ALL`. Explicit `off` entries still apply. Once enabled,
  `architecture/uncovered-source` takes precedence over selection and severity settings.
- Normal checks are read-only. `--fix` writes permitted source-ordering repairs;
  behavior-changing TYP003 repairs also need `--unsafe-fixes` and review of falsy
  values. `--unsafe-fixes` alone writes nothing. `--staged` rejects `--fix`.
- Review new style policies before adopting them. MOD004 `modeling/str-enum-value-convention`
  follows `style.str_enum_values` (`explicit` or `auto`); choose the project's
  convention once and preserve serialized values. VIS006/IDM004 review forwarding
  methods and name aliases. TYP006 prefers deferred annotations; verify runtime
  annotation consumers before removing quotes or adding a future import.
  TYP007 reviews anonymous tuple types; repeated shapes can represent distinct
  concepts, so name records where field meaning benefits callers rather than
  merging unrelated types. The `max` of the CPX003 rule entry, in `rules` or
  in a style override's `rules`, sets the limit for that scope; after adding
  keyword-only parameters, update all callers, including tests and callbacks.
  These rules have no automatic rewrite.
- Keep shared conventions in `style` and package-specific choices in
  `style.overrides`. Enable an option's rule in an override's `rules` to check
  only those packages. `smelt context TARGET --format json` shows effective `style` and
  `rule_details`, with sources and exception reasons.
- In a uv workspace, keep `project.workspace: uv` in the root `smelt.yml`.
  Check the owning file before adding a local policy.

  | File | Policy |
  | --- | --- |
  | Root `smelt.yml` | Project setup, shared `style` and `rules`, tests, import graph and `architecture.members` |
  | Local `extends` base | Optional root defaults. Mappings merge, lists replace and rules apply in layers. |
  | Member `smelt.yml` | Local layers, features and package APIs. Root style and rules are inherited. Add local style/rule exceptions, ignores, exclusions or debt here. |

  Keep package APIs and `style.overrides` with their owning project or member.
  A member without local layers still inherits shared style. Boundaries between
  members live in root `architecture.members` (ARC009).
  Declare imported member distributions in `project.dependencies` or
  `project.optional-dependencies` and configure their uv sources (ARC010);
  a source entry alone is not a dependency declaration. Both checks use direct
  production imports. `smelt check --statistics --by member` counts findings,
  including hints, by member; JSON findings carry the member path.
  Do not duplicate the member list or give a member an independent full config.
  `smelt check` in a member directory checks that member under the root policy.
  Run the root check before finishing work that changes shared packages. A
  member override that lowers root severity must use
  `{severity: warning, reason: "specific justification"}` (or `off`/`hint`).
  Use the same rule syntax in `style.overrides.<name>.rules`.
  For member style changes, use `{value: ..., reason: "specific justification"}`.
  `on` uses the catalog
  severity, and a winning individual entry needs its own reason. Strict coverage
  cannot be lowered even with a reason. Review thresholds and architecture
  replacements separately as policy decisions.
  `smelt init` drafts root policy and one architecture per Python member;
  `init --member <workspace-relative-path>` drafts only that member under an
  existing root. Review every draft before using `--force`, which overwrites
  all selected config targets. No target is written if another already exists.
  A member may set its own member-relative `debt`; stored finding paths remain
  workspace-relative. Root debt covers files without a member baseline. From
  a member, `smelt debt` affects only that member; root invocations maintain
  per-member files. `--prune` never creates a new member scope or accepts new debt.
- Do not disable checks, broaden exclusions or record debt merely to pass a check.
  When intentionally adopting a baseline, `smelt debt` records current findings
  and changes configuration. `smelt debt --prune` removes resolved debt without
  accepting new findings; `smelt debt --show` inspects stored debt read-only.
- For an intentional local exception, name the specific rule and a reason, for example
  `# smelt: ignore[architecture/layer-boundary] -- migration tracked in #123`.
  Rule codes also work. Statement comments do not
  suppress a whole function body. Keep exceptions narrow and address unused ones.
- Exit codes are `0` for passing, `1` for findings at the failure threshold, `2`
  for usage/configuration/analysis errors. Hints do not fail a check. JSON check
  reports use schema 2; inspect scope, comparison and coverage before interpreting
  `passed`. Changed/staged checks report new findings under current/index policy,
  including effects elsewhere; they do not inventory all existing violations.
- Smelt never replaces tests, Ruff or a type checker. It analyzes source statically;
  use a Python interpreter that can parse the project. Read each rule's inference
  limits before changing a public API or deciding a finding is a false positive.

## Read the relevant offline reference

The following files are copied/generated from the release's official documentation.
Load only the guide and individual rule pages needed for the task.
In the Smelt source checkout, edit rule metadata or original guides and run
`uv run python scripts/generate.py`, then `uv run python scripts/generate.py --check`.
Never hand-edit the generated rule pages or this skill's `references/`.
Installed consumer copies refresh through `smelt skill install --force`.

| Task | Reference |
| --- | --- |
| Install Smelt or start a policy | [Quick start](references/README.md) |
| Find commands, flags, outputs and scope semantics | [CLI and reports](references/docs/cli.md); also `smelt <command> --help` |
| Select rules and understand precedence | [Activation](references/docs/rule-selection.md) |
| Look up every rule, its rationale, examples, repair and config | [Complete catalog](references/docs/rules/README.md); follow its rule page links |
| Configure roots, inheritance, overrides or editor completion | [Configuration](references/docs/configuration.md); `smelt config schema` prints the complete editor schema |
| Design features, layers, dependencies and coverage | [Architecture](references/docs/architecture.md) |
| Configure package APIs, visibility and source style | [Structure](references/docs/structure.md) |
| Configure test layout, mocks and test-quality checks | [Testing](references/docs/testing.md) |
| Adopt a baseline or reasoned exception | [Debt and suppression](references/docs/adoption.md) |
| Set up hooks, agents or CI | [Integrations](references/docs/integrations.md) |
| Upgrade or interpret changed behavior | [Upgrade guide](references/docs/upgrading.md), [changelog](references/CHANGELOG.md) |

For first-time installation use `uv tool install smelt-cli`, then
`smelt skill install` in the target project. Only run `smelt init` (or
`smelt init --maintainability`) when a new policy is needed; review the inferred
configuration. `init --force` overwrites policy. An installed skill is replaced
only on an explicit `skill install --force`, including local edits and obsolete
references. Put project-specific instructions outside the installed skill.
