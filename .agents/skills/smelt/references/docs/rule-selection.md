# Rule activation and migration

`rules` is the only switch that enables a rule. Each named setting records a
policy decision:

```yaml
rules:
  layout/definition-order: error
  visibility/internal-public-member: warning
  tests/missing-outcome-check: warning
  tests/unbounded-wait: warning
  tests/missing-await-assertion: warning
```

## Families

Every rule name starts with its family. Enable an individual rule by canonical
name or stable code. The value `on` uses its catalog severity.

Use `smelt check --select typing` or `smelt check --select ALL` to explore the
catalog. In YAML, families and `ALL` can turn rules `off` or cap the
severity of rules already enabled by an individual entry. A broad severity with
no enabled matching rule is a configuration error.

```yaml
rules:
  architecture/layer-boundary: error  # Check the declared layers.
  architecture/import-cycle: on       # Enable this named rule at its catalog severity.
  architecture: warning               # Cap both to warnings.
```

Precedence within a map is `ALL`, then family, then individual rule.
Style overrides restrict broad settings to source rules. Broad settings never
touch `architecture/uncovered-source` once it is enabled.

## Layered files

Use `extends: ../smelt.base.yml` for shared, versioned policy. Each file in an
`extends` chain is its own rules layer. A later named setting can replace an
earlier setting. A broad setting can disable or cap matching rules. Use
`smelt config show --sources` for the policy origins and `rule_selectors` matches.

## Information hiding

The visibility reviews cover functions and methods (VIS001), module constants
and type aliases (VIS002), data attributes (VIS004) and APIs whose only observed
consumers are tests (VIS009 and VIS010). Enable the ones your project uses by
individual name. Explore these five together with:

```sh
smelt check --select VIS001,VIS002,VIS004,VIS009,VIS010
```

The static checker cannot prove a rename is safe. Preserve deliberate public
interfaces and review external consumers before editing them.

## Workspace members

In a [uv workspace](configuration.md#uv-workspaces), a member's `smelt.yml` is
one more rules layer. It applies by path to the member's sources and tests:

1. the root rules layers, bases first;
2. the member's `rules`, from the innermost member that contains the file;
3. the closest `style.overrides` entry that sets the rule.

```yaml
# smelt.yml at the workspace root
rules: {complexity/duplicate-function: warning}
```

```yaml
# packages/legacy/smelt.yml
rules:
  complexity: {severity: off, reason: Migrating the legacy service.}
  complexity/duplicate-function: {severity: hint, reason: Review duplication before enforcement.}
```

Here `packages/legacy` reports only `complexity/duplicate-function`, as a hint.
Every other member keeps `complexity/duplicate-function: warning`. Within a member file, the most
specific selector wins, as in any other layer. A rule that only a member enables
stays off in the other members.

A lower member severity needs `{severity: ..., reason: "..."}`, including in
member style overrides. The winning selector supplies the reason. `on` is
compared using the catalog severity. See [reasoned member exceptions](configuration.md#reasoned-member-exceptions).

A member's `architecture` is checked by the architecture rules the root or the
member enables, for example `architecture/layer-boundary` checks every member's
`layers`. Each local architecture rule uses the architecture of the project that
owns its target. See [member architecture](configuration.md#member-architecture).

## Activation and precedence

The catalog distinguishes two modes, independently of severity:

| Mode | Activation |
| --- | --- |
| `default` | ARC011, suppression/debt hygiene MET001–MET003, and missing required rule policy MET004 |
| `explicit` | Every other rule; enable it with `rules` or CLI selection |

A policy describes what a rule checks; it never enables the rule. Declaring
layer dependencies and enabling `architecture/layer-boundary` are two separate
decisions:

```yaml
architecture:
  layers:
    domain: {modules: [app.domain]}
    adapters: {modules: [app.adapters], may_depend_on: [domain]}
tests:
  layout: mirror
rules:
  architecture/layer-boundary: error
  tests/misplaced-test: error
```

Two configuration diagnostics keep policy and rules in step:

- Enabling a rule whose policy is required but missing is a configuration
  error, for example `architecture/layer-boundary` without `architecture.layers`.
  `smelt explain` names each rule's policy.
- A declared policy whose rule no `rules` entry names is a configuration
  warning, so `--fail-on warning` fails until you enable the rule or set it to
  `off` to keep the policy unchecked.

Some rules have built-in defaults and run without their option, for example a
limit of 5 for CPX003. Set under `style`, an option covers the project; a style
override covers the matching packages. A rule whose policy is required reports
nothing in packages without that policy. See
[project-wide options](structure.md#project-wide-options-and-package-overrides)
for the defaults. Use `smelt context MODULE --format json` to inspect the target's
`rule_details`. Entries show the effective severity and whether it came from
`rules` or the catalog baseline, along with the source and reason when available.

```bash
smelt rules --format json
smelt explain visibility/internal-public-member
smelt check --select visibility,tests/unbounded-wait
smelt check --select VIS001,TST004
smelt check --select ALL --ignore architecture/import-cycle
```

`--select` replaces normal selection and accepts canonical names, families,
stable code prefixes and `ALL`. `--ignore` accepts the same selectors. A selected
rule with a required policy reports nothing where that policy is missing.

Project `rules.<name>: off` disables that rule even under explicit selection.
Package overrides use the same rule names, and the closest package entry wins;
they can disable a global rule, change severity or enable it locally. CLI ignores
apply across the run. Duplicate name/code entries and conflicting settings for
the same concrete package are config errors.

`architecture/uncovered-source` (ARC013) is a guard. Enable it with
`rules: {architecture/uncovered-source: error}` in the root config, or for one
run with `--strict-coverage`. Once enabled it stays enabled outside a select
prefix and always reports errors. Lowering its severity, ignoring it, recording
it as debt or enabling it in a workspace member is rejected. Invalid
configuration, discovery and source-analysis errors remain fatal independently
of selection.

Debt for inactive rules or disabled packages remains unassessed. Recording and
pruning retain it; disabling checks does not mean violations were fixed. Hint-only
debt is also retained by the debt command. Changed checks compare source under
the current explicit policy on both sides.

Use `smelt init --maintainability` to write a visible selection of source/test
reviews by individual name, with optional hints commented out. Use
`smelt rules --unconfigured` to discover rules to review. Plain `init` infers architecture,
enables the rules that check the inferred policy and leaves conventions opt-in;
both commands write an editor schema beside the YAML. The complete small-project
API, package exceptions, editor setup and removed-field/name mapping are in
[configuration and migration](configuration.md). See the
[generated catalog](rules/README.md) for every rule's activation decision.
