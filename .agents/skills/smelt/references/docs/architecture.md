# Architecture as code

## A DDD layout


```yaml
version: 2
project:
  root_packages: [backend, agent]
  source_roots: [backend/src, libs/agent/src]
  test_roots: [backend/tests, libs/agent/tests]

architecture:
  features: {root: backend.features}     # every child package is a feature
  shared: [backend.shared, backend.env]  # importable everywhere, imports no feature
  composition_root: [backend.main, backend.app]
  wiring: [backend.features.*.infrastructure.di]
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
    infrastructure:
      path: infrastructure
      modules: [backend.platform, agent]  # packages outside the features
      may_depend_on: [domain, application]
    presentation: {path: presentation, may_depend_on: [application, domain]}
  cross_feature:
    default: deny
    allow:
      - "application -> application"     # between all features
      - {from: session.presentation, to: auth.presentation}

tests:
  layout: mirror
  mirror: "{root}/{path}/test_{module}.py"
```

- **Layers** hold inside every feature: `domain` must not import `infrastructure`.
- **Features** may only import each other through `cross_feature.allow`. The string form
  allows a layer pair for all features, the object form one direction between two.
- **Central packages** such as a platform or a workspace library belong in their
  layer's `modules` list. The layer rules then apply between them and the features (a feature's domain
  must not import `backend.platform`), and they must not import features.
- **Wiring** modules (whole-segment `*` allowed) keep their feature and layer and may cross
  layer boundaries. Feature wiring may also cross feature boundaries. Composition roots,
  other wiring modules and their package facades may import declared wiring. Central or
  shared wiring keeps the prohibition on importing features.
- Every module should belong somewhere: an unclassified module is exempt from all
  layer and feature boundary checks, so ARC012 warns about it. Composition-root/wiring
  import restrictions and configured cycle checks still apply.

`smelt init` infers most of this: features, layers, shared and settings modules, the
composition root including an app factory (imported or named as a `"backend.app:app"`
server target), wiring patterns, central packages by name and
the mirror pattern the existing tests follow. Review it before adopting its findings.
It follows the names a composition root imports through package facades into their
definitions (`FEATURES = (chat.feature, ...)`); a module whose definition is built from
providers, such as a `feature.py` assembling `TelegramProvider`, becomes wiring, and the
config comment shows the chain. Merely importing framework types changes nothing. An
unclassified package only the root uses is proposed as a commented-out candidate with its
chain and confidence, never applied silently.
In a uv workspace it lists every declared member with the packages it found or why it
skipped it (`tool.uv.workspace.exclude`, no Python package); namespace packages without
`__init__.py` count under `src/` and where `tool.uv.build-backend` declares them.
Its starter policy explicitly allows third-party packages and checks direct imports.
Review these decisions: to keep frameworks out of the core, set e.g.
`domain.third_party: {default: deny, allow: [pydantic]}` under `architecture.layers`.
Set `architecture.import_graph.transitive: true` to check indirect dependencies through
all reachable project modules, including helpers, facades and allowed layers:

```yaml
architecture:
  import_graph:
    transitive: true
  layers:
    domain:
      path: domain
      third_party: {default: allow, deny: [sqlalchemy, fastapi]}
```

A chain such as `app.domain.model → app.shared.utils → sqlalchemy` now violates
the domain's third-party policy even though its own imports look clean. Layer,
cross-feature, shared and composition-root rules also check reachable targets.
Findings point at the originating import and include a deterministic shortest
chain; JSON includes the source line of every hop. Cycles terminate safely, and
`architecture.import_graph.type_checking: ignore` removes guarded imports at every hop.
Direct violations are not repeated as indirect ones for the same target.

This is a module-graph check: importing one name from a facade also makes the
facade's other imports reachable. A layer's `may_depend_on` and cross-feature
allowances must permit all reachable targets when transitive checking is enabled.
The package's public-entry-point contract ([ARC007](rules/ARC007.md)) still
allows intentional symbol re-exports. External packages are leaves; smelt does
not inspect their implementation or infer dynamic imports. No `import-linter`
dependency or separate contracts are needed for these indirect checks.
Transitive checking remains off by default, including in starter policies.

The starter leaves `cross_feature.allow` empty; declare intended
feature relationships explicitly. A global `application -> application` pair permits
that dependency between **every** feature.

`smelt context` describes the policy that applies to its target, including wiring and
composition-root exceptions, third-party defaults and direct/transitive coverage. It
accepts feature names, dotted module names and source paths, including planned `.py`
files under existing source packages. For a new feature, context projects the feature
and layer from the declared architecture and sets `feature_planned` in JSON. If a
syntax error prevents analysis, the briefing
is still available and its violation counts are marked unavailable (`violations: null`
and `analysis_error` in JSON). Central and wiring modules have explicit JSON flags.


## Libraries and mixed package layouts

When a layer spans several packages, declare their fully qualified module names
in `architecture.layers.<layer>.modules`. A package prefix covers all descendants.
Use a more specific prefix for a subtree or file that belongs to a different layer.

```yaml
architecture:
  layers:
    values:
      modules: [sdk.diagnostics, sdk.errors, sdk.error.types]
    adapters:
      modules: [sdk.error, sdk.openapi]
      may_depend_on: [values]
```

Here `sdk.error.registry` inherits `adapters` from `sdk.error`, and
`sdk.error.types` belongs to `values`. `path` is optional for layers assigned this
way. You can set both keys when a layer also has a repeated path inside features.
Explicit module bindings cover central code outside the feature grid and take
precedence over relative `path` matching. The most specific prefix wins, and
conflicting layers for the same prefix are rejected. A child assignment that
repeats its nearest parent's layer produces an advisory configuration warning.

Before adding a file, run `smelt context sdk/error/registry.py --format json`.
`layer_assignment` identifies the matching prefix, declaring file, config key
and whether the assignment was inherited. An inherited match needs no extra YAML.
When the new code belongs to another layer, add the narrowest intended prefix to
that layer's `modules` list. Run `smelt check --strict-coverage --fail-on warning`
to verify placement and import boundaries.

## Strict policy coverage


Enable `architecture/uncovered-source` in the root `rules`, or use
`smelt check --strict-coverage`, to fail when production source escapes package
selection or has no architecture role.
Folder names, layer names and frameworks come from your policy. Flat libraries,
feature-only architectures and custom layers work without adopting a DDD layout.
Outside uv workspaces, declare the relevant import roots in `source_roots`.
Unreadable or invalid workspace metadata is an analysis error in strict mode;
configured mode records discovery uncertainty and marks coverage incomplete.
The inventory discovers Python files independently of `root_packages`, under the
configured source roots and declared uv workspace members. Packaging exclusions
in `tool.uv.workspace.exclude` do not hide source from this inventory.

```yaml
project:
  root_packages: [backend, client]
  source_roots: [backend/src, libs/client/src]
  exclude:
    - path: "libs/client/src/client/generated/**"
      reason: "Generated SDK; regeneration and drift are checked separately."
rules:
  architecture/uncovered-source: error
```

Each discovered source has one status:

- `governed`: a declared layer, feature-only policy, shared, central, wiring or
  composition-root role applies.
- `facade`: a package initializer containing imports, documentation and package
  metadata, including guarded imports and the standard package-version lookup.
  Implementation functions and classes in an unclassified initializer are gaps.
- `unclassified`: analyzed source has no architecture role.
- `uncovered`: source exists outside the selected packages or is not an importable
  module. The diagnostic names its package and import root where possible.
  Loose scripts need a governed package or a reasoned tooling exclusion;
  `root_packages` selects package directories rather than individual Python files.
- `excluded`: an explicit project exclusion applies. Strict mode requires a
  `{path, reason}` entry with a nonempty reason; string globs remain supported in
  the default `configured` mode.

`coverage.complete` means every discovered production source has a policy, is a
package facade, or has a reasoned exclusion. It describes policy assignment;
`scope.rules` and `coverage.imports` describe the checks actually run. Correct
behavior still needs type checking and tests. Configured test roots, workspace
`tests/` and `test/` directories, hidden directories and environment/cache
directories are outside production discovery. Independent runtimes outside the
declared roots/workspace need their own configuration and check.

The normal JSON report includes counts and scan roots. Add `--show-coverage` for
the per-file inventory, including feature, layer, source root, policy references
and exclusion reason. Text output groups related missing files into one decision
while JSON retains every path. The text summary also reports incomplete policy
assignment in `configured` mode, which preserves the existing exit behavior.

Strict coverage findings (`ARC013`) remain errors even in `--changed`, path-scoped
and `--select` runs. Inline/config ignores and debt cannot hide them; disabling or
demoting ARC013 in strict mode is rejected. Recording or pruning debt requires
complete coverage first. Resolve the placement or document a deliberate exclusion.

`smelt context <path> --format json` includes the file's coverage status and policy.
It also explains uncovered source discovered outside `root_packages`. A planned
path reports whether its placement has a role without claiming the file has been
analyzed. Use this before writing code, then run the strict check after editing.
