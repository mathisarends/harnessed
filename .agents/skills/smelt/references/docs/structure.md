# Package APIs and source conventions

## Project-wide options and package overrides

Source conventions are defined once. Set an option directly under `style`
to apply it to the whole project, and in a style override to apply or override
it for matching packages. The closest style override that sets an option wins, then
`style`, then the built-in default:

```yaml
style:
  future_annotations: when-needed
  dataclass_options: [slots]
  overrides:
    legacy:
      match: app.legacy
      dataclass_options: []
```

Options configure rules; they never enable them:

- Enable the rule in `rules` or with `--select`. Under `style` an option applies
  to the whole project; under `style.overrides` it applies to the matching
  packages. Where no option is set, the built-in default applies.
- To check an option only in some packages, enable its rule in that style
  override's `rules` instead of the project `rules`.
- An option whose rule no `rules` entry names is a configuration warning, so
  `dataclass_options: [slots]` without `modeling/missing-dataclass-option` does not go
  unchecked silently.
- `off` in `rules` always wins, including in a style override's `rules`.
- `ordering` configures `layout/definition-order`, but the rule is enabled
  through `rules` like any other fixed pattern.

| Option | Rule | Default when the option is unset |
| --- | --- | --- |
| `imports` | IMP003 | none; nothing is checked without a style |
| `reexports` | VIS008 | `all` |
| `module_import_exemptions` | IMP002 | `[facades, composition_root]` |
| `module_docstrings` | LAY002 | none |
| `future_annotations` | TYP001, TYP002 | none |
| `private_modules` | LAY003 | none |
| `public_attribute_boundary` | VIS004 | `class` |
| `private_attribute_boundary` | VIS005 | `module` |
| `type_dispatch` | IDM002 | none |
| `navigation` | CPX001 | none |
| `call_argument_kinds` | CPX002 | `[await, comprehension, aggregate]` |
| `str_enum_values` | MOD004 | `explicit` |
| `dataclass_options` | MOD006 | none |
| `bases` | MOD007 | none |
| `third_party_reexports` | IMP004 | none |
| `errors` | MOD008 | none |

[CPX001](rules/CPX001.md) only accepts namespace evidence it can read in analyzed
code. `navigation` declares it for code Smelt cannot analyze, using the qualified
names the project imports:

```yaml
style:
  navigation:
    # Every attribute of an SDK client is a domain API: client.target.close_target()
    namespace_types: [cdpify.CdpClient]
    # State(Counts) and needs(Service) return an instance of their first argument
    instance_factories: [framework.State, framework.needs]
```

With `instance_factories`, a class attribute `_state = State(Counts)` or a
constructor assignment `self.state = State[Counts](https://github.com/mathisarends/smelt/blob/main/docs/Counts)` has the type `Counts`,
so the record and container rules apply to it. A service made by `needs(Service)`
stays a collaborator.

`architecture.packages` sets `required`, `public`, `min_imports_to_bundle`,
`min_importers_to_export` and `count_tests`. Use `style.overrides` for
conventions and package-level `rules`. Top-level `rules` set project-wide
severities. `smelt context` shows the source of each effective option.

## Limits

Rules that count something take their limit as `max` in their own `rules`
entry rather than as a `style` option:

```yaml
rules:
  complexity/excess-positional-parameters: {severity: warning, max: 3}
style:
  overrides:
    legacy:
      match: app.legacy
      rules:
        complexity/excess-positional-parameters: {max: 6}
```

An entry in a style override's or workspace member's `rules` may set only
`max`: it keeps the severity inherited from the enclosing scope and refines the
limit for those files. A `max` without a severity where no enclosing scope
enables the rule is a configuration error. Families and `ALL` take no `max`.

| Rule | Default `max` |
| --- | --- |
| [IMP002 `imports/excess-module-imports`](rules/IMP002.md) | 20 |
| [CPX003 `complexity/excess-positional-parameters`](rules/CPX003.md) | 5 |
| [CPX006 `complexity/excess-comprehension-loops`](rules/CPX006.md) | 1 (minimum 1) |
| [CPX007 `complexity/excess-constructor-decisions`](rules/CPX007.md) | 2 |
| [VIS007 `visibility/excess-private-definitions`](rules/VIS007.md) | 12 |
| [TST005 `tests/excess-patches`](rules/TST005.md) | required; project-wide only |

## Source conventions

Opt-in [CPX002 `complexity/inline-call-argument`](rules/CPX002.md) reviews
three kinds of direct call arguments: awaited results (`await`), list, set and
dict comprehensions (`comprehension`) and builtin `all`, `any`, `max`, `min`,
`sorted` or `sum` reductions of a generator (`aggregate`). Enable
`rules: {complexity/inline-call-argument: hint}` to ask for named intermediate
values before construction or consumption. `call_argument_kinds` (all three
by default) narrows the review:

```yaml
style:
  call_argument_kinds: [await]
```

Unlike CPX006, comprehensions include simple mappings: the concern is separating
calculation from the consuming call. Generator expressions passed directly,
starred arguments and conditionally or repeatedly evaluated expression contexts
are excluded; a reported reduction is named as a whole so its generator stays
lazy. Each finding carries its `kind` in the machine-readable payload. No
automatic rewrite is performed; preserve callable lookup and argument evaluation
order when extracting a variable.

Opt-in [CPX008 `complexity/inline-constructor-receiver`](rules/CPX008.md) reviews
direct calls such as `PointerTools().tool(...)`. To require a named instance
before the method call, enable
`rules: {complexity/inline-constructor-receiver: warning}`. It checks call
statements, assignments and returns in production code, using same-module class
declarations or class-like import names as evidence.

Opt-in [VIS004 `visibility/internal-public-attribute`](rules/VIS004.md) reviews
public data attributes whose observed use stays within the owning class. Set
`public_attribute_boundary: module` under `style` or in a style override to
enable the rule and also treat reads and writes by same-module helpers or other
classes as internal. Set `class` in a closer override to use the class boundary
there. A possible access from another production module preserves visibility,
as do explicitly exported classes. [VIS010](rules/VIS010.md) uses the same
boundary when reviewing test-only reads.

Opt-in [VIS005 `visibility/foreign-private-access`](rules/VIS005.md) complements
Ruff SLF001 with statically resolved ownership. Enable
`rules: {visibility/foreign-private-access: warning}`. Imports and module-qualified
reads of another module's private symbols (`kind: symbol`) are reported, as are
known reads, writes, calls, updates and deletes of private members owned by
another module (`kind: attribute`); same-module cooperation, direct
own/inherited receiver accesses and dunders are allowed. Tests remain the
responsibility of TST002. Unresolved receivers are skipped.
`private_attribute_boundary` defaults to `module`; `package` permits
attribute cooperation within the same immediate Python package, including its
initializer. Private module symbols always use the module boundary.
Subpackages remain separate boundaries. Set the option under `style` or in a
style override, and `smelt context` shows the effective value.
No automatic rewrite is performed. Review the documented inference limits before
depending on the review as an error gate.

Opt-in `complexity/excess-comprehension-loops` (CPX006) reviews list, set, dict and generator
comprehensions with multiple iteration or decision steps. Enable
`rules: {complexity/excess-comprehension-loops: hint}`; a `max` (default 1)
in the entry, or in a style override's entry, permits intentional flattening,
for example in one package. Simple filters and conditional elements with names, attributes
or constants on both branches remain valid. See [CPX006](rules/CPX006.md) for the
exact triggers and repairs that preserve generator laziness.

Opt-in `complexity/excess-constructor-decisions` (CPX007) reviews decision nodes in `__init__` and
`__post_init__`. Enable `rules: {complexity/excess-constructor-decisions: hint}`
and set its `max` (default 2) there or in a style override's entry. Zero forbids counted decisions. A single fallback or guard remains valid at the default threshold;
nested helper bodies are excluded. See [CPX007](rules/CPX007.md) for the exact
counting rules and repairs that preserve initialization behavior.

Opt-in [VIS007 `visibility/excess-private-definitions`](rules/VIS007.md) reviews modules with
many private supporting definitions. Enable `rules: {visibility/excess-private-definitions: hint}`.
Its `max` (default 12, zero allowed), in the entry or in a style override's
entry, sets the limit for that scope.
Private classes, sync/async functions and PEP 695 type aliases directly in the
module body count once per kind and name. Private uppercase assignments with values
are listed separately and never contribute to the limit. Imports, methods,
nested/local and guarded definitions, dunders and ordinary assignment-style type
aliases are excluded. A few models beside their consumer remain valid; even a
larger cohesive model tree or parser can be intentional. Review the listed
definitions for cohesive groups, carrying constants and validators with them and
preserving import direction and visibility. No groups or safe moves are inferred;
there is no automatic rewrite or required `models.py` filename.

Choose canonical configuration filenames once per project:

```yaml
style:
  file_naming:
    yaml_extension: yaml        # yaml or yml, without a dot
    compose_basename: compose   # compose or docker-compose
```

[LAY007](rules/LAY007.md) reports files that use another YAML extension or
Compose basename, with one finding per file that lists every correction. The two
policies are independent: omitting either allows both spellings for that part of
the name.
No separate `rules` entry is needed; use one to adjust severity or disable a check.

Checks cover lowercase `.yaml` and `.yml` files below the directory containing
the Smelt configuration, including that configuration, `.github/workflows` and
deployment directories outside Python source roots. Tool/cache directories and
`project.exclude` paths are skipped. Dotted Compose variants such as
`docker-compose.override.yml` retain their variant; custom names such as
`stack.yaml` are not classified as Compose files. File contents are not parsed.

Findings give the expected path. Rename deliberately, resolve existing target
files and update references in CI, scripts, `extends` and `docker compose -f`
arguments. Smelt does not rename files or update references automatically.
Use reasoned `ignore.paths` entries for required external filenames.
`smelt context` includes the declared filename policy; path-scoped, changed and
staged checks apply to these files too.

The [pre-commit benchmark](https://github.com/mathisarends/smelt/blob/main/docs/agent-workflow.md#canonical-configuration-filenames)
records the additional cost with 1,000 YAML files, including complete hook timings.

Prefer private module functions for class-internal static helpers by enabling:

```yaml
rules:
  layout/misplaced-static-helper: warning
```

`smelt check --select LAY005` reviews helpers such as `_build_match_clause` and
`_created_at_sort` when they need no instance or class state. Findings include a
collision-free `suggested_name`, the destination module, observed internal
references and `automatic_extraction: false`. Move the helper deliberately and
update its class-qualified callers. Decorated, inherited or subclassed classes
and external static APIs are conservatively retained. A missing finding in those
cases does not mean the rule is inactive; inspect its documented scope with
`smelt explain LAY005`.

To bundle a facade import with its implementation imports, set a package API
threshold explicitly:

```yaml
architecture:
  packages:
    application-api:
      match: backend.features.*.application
      min_imports_to_bundle: 2
```

IMP001 reviews `from ...application import PasswordHasher` alongside
`from ...application.errors import AccountAlreadyExistsError`. Its JSON finding
includes `proposed_exports` for missing static re-exports, or `blocked_exports`
and a `cycle` witness when the proposed facade edges close a known runtime cycle.
After reviewing the exports, callers can import both names from `application`.
Other APIs such as `auth.domain.validation` do not contribute to this threshold.
No source is rewritten. `no-cycle-found` describes the explicit runtime import
graph and does not prove package initialization or dynamic imports safe.

Declare a repeated structure or public interface under `architecture.packages`.
Package APIs apply to Python packages matched by a dotted pattern, independently of feature or layer
names. A whole-segment `*` in `match` selects one package segment.

```yaml
architecture:
  packages:
    feature-shape:
      match: app.features.*
      required: [application.api, domain]
      public: [application.api]
    library-api:
      match: toolkit
      required: [models]
      public: ['.']
```

This requires every matching feature to contain an `application.api` module or
package and a `domain` module or package. Callers outside that feature may import
only `application.api`. Internal code may use its own implementation modules.
`toolkit` exposes its package facade; `toolkit.models` remains private to outside
callers, while the facade can deliberately re-export symbols from it.

- `required` contains exact relative dotted module names. Namespace packages and
  split source roots work too. Excluded files do not fulfill a package requirement.
- `public` entries are relative module patterns. `'.'` means the matching package
  itself; `api` exposes exactly that module/package, and `api.**` deliberately
  exposes that subtree. Each pattern must match analyzed code. Omit `public` to
  check structure only; `public: []` closes the package to outside imports.
- Package APIs are additive. An import must satisfy every applicable interface.
  Wiring and composition roots follow the same interface rules. Existing layer
  and feature restrictions still apply, and public entries do not classify code
  into architecture roles or grant cross-feature access.
- ARC014 reports missing required modules or public entry points. A `match`
  pattern selecting no analyzed package is a configuration error. ARC007 reports
  private imports, including explicit module-valued aliases through facades;
  intentional symbol re-exports through a public entry point are permitted.
- These are static module policies. They do not check `__all__`, arbitrary
  attribute access, dynamic loading, symbol-level visibility, or behavior. Module
  presence alone does not establish a useful implementation; choose required modules
  that reflect an intentional design, rather than creating empty template files.

`smelt context` shows the applicable package APIs, including required/public
modules, missing entries and the visibility of the target module. Check JSON includes
resolved package policies under `coverage.contracts`. `smelt explain ARC007`
and `smelt explain ARC014` describe the rules.

## Positional parameter limits

Make later arguments keyword-only for the whole project, or for one package
with a style override.

```yaml
rules:
  complexity/excess-positional-parameters: {severity: warning, max: 3}
style:
  overrides:
    named-arguments:
      match: app.api
      rules:
        complexity/excess-positional-parameters: {max: 2}
```

[CPX003](rules/CPX003.md) reports definitions accepting more than that limit:
`connect(user_id, run_id, tab_id)` becomes
`connect(user_id, run_id, *, tab_id)` and callers supply `tab_id=...`.
Zero requires all ordinary parameters to be keyword-only. `self`/`cls`, variadic
arguments, dunders, overload/override definitions and tests are exempt.
Enabling CPX003 without a `max` uses a limit of 5; the closest style
override's `max` wins.
`smelt context` exposes the effective limit, including for planned paths.
Update every caller and partial application; keep externally dictated callback
signatures with a reasoned local exception. There is no automatic rewrite.

## Aliases, delegates and annotation records

These reviews are independent and opt-in:

```yaml
rules:
  visibility/redundant-private-delegate: hint
  idioms/redundant-name-alias: hint
  typing/prefer-deferred-annotations: hint
  typing/anonymous-tuple-type: hint
```

[VIS006](rules/VIS006.md) reviews methods that only forward unchanged arguments
to one exclusive private helper. [IDM004](rules/IDM004.md) reviews module-level
aliases that only repeat a class or function name. Review external consumers,
decorators and framework contracts before inlining or removing an alias.

[TYP006](rules/TYP006.md) reviews quoted annotations. With the annotations future
import or a declared Python 3.14+ minimum, remove redundant quotes; on older
targets, consider the future import first. Verify runtime annotation consumers
such as Pydantic and dependency injection. A package that forbids future
annotations keeps quotes before 3.14. Runtime strings, `Literal` values and
`Annotated` metadata are excluded.

[TYP007](rules/TYP007.md) suggests named records for fixed tuple types with
repeated element types or at least three fields. Prefer a `NamedTuple` or frozen
dataclass when field names clarify a record's meaning. Composite mapping keys,
source spans and version pairs may intentionally retain tuple semantics. The
rule excludes variable-length tuples, two distinct element types, quoted
annotations and externally defined override/dunder/overload signatures.

These four rules skip tests and perform no automatic rewrite. Read each rule's
scope and valid counterexamples before deciding on a repair.

## Module names without private prefixes

Require descriptive module/package names while keeping private helpers inside
them:

```yaml
style:
  overrides:
    module-names:
      match: app
      private_modules: forbid
```

LAY003 (`layout/forbidden-private-module`) rejects `_service.py`, `_internal/` and other
underscore-prefixed module/package names, including double-underscore names.
Python's special `__init__.py` and `__main__.py` files are allowed; arbitrary names
such as `__service__.py` are still checked. This exception applies to files, not
dunder-named package directories.

Use `service.py` with public entry functions and `_helper` functions instead.
The rule checks names independently of exports: a private-prefixed module is
still a finding when it declares `__all__` or its symbols are re-exported. Function,
method, class and attribute names are unaffected. This naming convention does
not change the public entry points declared under `architecture.packages`.

Omitting `private_modules` adds no naming policy. A more specific package can set
`private_modules: allow`; contradictory policies on the same concrete package
are config errors. Existing reasoned `ignore` entries can cover individual legacy
modules. Excluded source and test directories follow the normal analysis scope.
Namespace packages and split import roots are supported, with one finding per
offending logical package rather than duplicate findings for its portions.

`context` exposes the effective convention as `naming_policy`, including planned
files. Check JSON includes it in `coverage.contracts`. Rename deliberately and
update imports and facade exports; Smelt never renames files or symbols itself.

## Separate instance attribute assignments

To keep field initialization readable, opt into `IDM001`:

```yaml
style:
  overrides:
    instance-fields:
      match: app
      rules:
        idioms/bundled-instance-assignment: error
```

This reports `self.log, self.control = log, control` and chained assignments such
as `self.log = self.control = value`. Prefer:

```python
self.log = log
self.control = control
```

The rule checks assignments writing multiple direct attributes of an instance
method's first positional parameter, including differently named receivers and
async methods. Local unpacking stays valid. Mixed unpacking with only one direct
instance target is allowed; nested object attributes and assignments in nested
helper bodies are outside the check. Nested classes use their own receiver.
Recognized static/class methods are excluded; arbitrary decorator and assignment
aliases are not inferred. Only analyzed production modules are covered.

The closest package rule wins; `rules: {idioms/bundled-instance-assignment: off}` exempts
a package. Effective rule severities appear in `context`, including planned modules.
This is an optional readability convention. There is no autofix: parallel
assignment, properties, swaps and unpacking failures can change behavior when
split. When unpacking a computed pair, consider unpacking into locals first and
then assigning each field while preserving evaluation timing.

## Type dispatch with match/case

`IDM002 idioms/prefer-match-type-dispatch` suggests reviewing longer positive
`isinstance`/`elif` chains over the same simple name:

```yaml
style:
  overrides:
    event-dispatch:
      match: app
      type_dispatch:
        style: match
        min_branches: 3
```

The default threshold is three type-tested branches; it counts branches rather
than members of a type tuple/union. The closest policy wins. Use `style: allow`
for an exception and `rules: {IDM002: error}` if this convention must fail CI.
The default severity is a hint, since class-pattern equivalence needs review.

```python
match event:
    case OutputValidated() | OutputValidationFailed():
        if event.call_id is not None:
            control_calls.add(event.call_id)
    case ToolCalled() if event.call.id not in control_calls:
        calls[event.call.id] = event.call
        yield ToolStarted(event.call)
```

An independent preceding `if isinstance(event, SessionEvent): events.append(event)`
must stay independent: an event may need both observation and dispatch. An inner
body condition must not become a case guard without checking fallthrough: when
an OutputValidated event has no call ID, its original branch still consumes it.
Existing and-guards are recorded separately from body conditions.

Supported heads use the builtin isinstance or an unambiguous top-level builtins
import alias, a simple subject name and type names/dotted names/tuples/unions.
Additional and-guards are allowed. Independent if statements, mixed subjects,
negative/or tests, type factories, parameterized types, computed subjects and
unsupported branches are not combined. One hint describes the whole supported
chain; elif suffixes are not reported independently.

Checker names bound anywhere in a file are conservatively excluded unless they
are a single recognized top-level builtin import. A parameter in another function
can therefore suppress the same checker spelling for the file. Star imports and
recognized builtin-module mutations also disable detection. Assignment aliases,
dynamic namespace changes and runtime type identities are not inferred; a type
name may refer to something unsuitable for a class pattern.

There is no autofix. Match evaluates its subject once, and subclasses, custom
instance checks, guard effects and runtime type values need manual review. Declared
Python minima before 3.10 are excluded; unknown minima retain an explicit version
review requirement. JSON exposes the subject, branch types, guards and Python
target; context exposes the policy for existing and planned modules. Only analyzed
production sources are covered. Changed/debt comparisons track branch identities
and multiplicity, so an additional branch cannot hide behind an accepted chain.
See the Python reference for
[match semantics](https://docs.python.org/3/reference/compound_stmts.html#the-match-statement).

## Relative and absolute imports

Declare the style explicitly, globally for a root package or for selected
packages. This example uses absolute imports by default and relative imports
within infrastructure packages where at most one leading dot is needed:

```yaml
style:
  overrides:
    default-style:
      match: app
      imports: {style: absolute}
    adapter-style:
      match: app.features.*.infrastructure
      imports: {style: relative, max_relative_level: 1}
```

In the relative scope, `from .models import Record` is preferred over
`from app.features.users.infrastructure.models import Record`. An import requiring
`from ..domain import User` instead uses the absolute path. The limit counts
leading dots; set `max_relative_level: 2` to permit and prefer parent imports up
to two dots. Imports between different root packages and third-party/standard
library imports stay absolute. `style: absolute` requires absolute imports even
for siblings. Omitting `imports` preserves either form.

The most specific enclosing package with an `imports` policy wins. Conflicting
styles for the same concrete package are config errors. Initializers and namespace
packages use their logical package, independently of filesystem source roots.
IMP003 reports the expected form without rewriting bindings or aliases. As with
other import rules, `architecture.import_graph.type_checking` controls guarded imports.
Ruff's [TID252](https://docs.astral.sh/ruff/rules/relative-imports/) already covers
forbidding parent-relative imports; Smelt additionally checks the preferred form
under these style overrides.

## Re-export spelling in package initializers

Set `style.reexports` and enable [VIS008](rules/VIS008.md) to check package
initializers. `all` uses ordinary imports and an explicit list of public names.

```python
from .models import Event

__all__ = ["Event"]
```

`alias` marks the re-export at the import. Keep `__all__` where callers depend
on its precise runtime contents, including for star imports.

```python
from .models import Event as Event
```

A package can choose a different style or allow either spelling when maintaining
an exact runtime export list. `smelt context app.plugins` shows the effective
value and the reason for the override.

```yaml
style:
  reexports: alias
  overrides:
    plugin-registry:
      match: app.plugins
      reexports: {value: allow, reason: Plugins are discovered via __all__.}
```

The rule uses direct, unambiguous import bindings. Real renames such as
`from .models import Event as PublicEvent` can use `__all__` in either style.
Selecting VIS008 directly uses `all` wherever the project has set no convention.

## Suggestions to bundle implementation imports

For total coupling across all project packages, configure
[IMP002 `imports/excess-module-imports`](rules/IMP002.md):

```yaml
rules:
  imports/excess-module-imports: {severity: hint, max: 20}
style:
  module_import_exemptions: [facades, composition_root]  # default; [] checks both
  overrides:
    core:
      match: app.core
      rules:
        imports/excess-module-imports: {max: 12}
```

The entry's `max` sets the limit, and the closest style override's `max`
refines it for its packages. Enabling the rule or selecting it with `--select`
without a `max` uses a limit of 20. `module_import_exemptions` without an
IMP002 entry in `rules` is a configuration warning.
Counts include direct internal imports across every root package, follow
`architecture.import_graph.type_checking`, and retain facade paths without following
re-exports to definitions. Repeated imports count once. Standard-library and
third-party imports never count. Package initializers and declared composition
roots are exempt unless `module_import_exemptions` removes that exemption globally
or in a style override. `[]` disables both exemptions. This design review has no
automatic rewrite: split cohesive responsibilities or review deliberate facades
and their import cycles.

Several imports from the same package can suggest a missing public facade:

```python
from app.features.users.application.get_user import GetUser
from app.features.users.application.update_user import UpdateUser
from app.features.users.application.delete_user import DeleteUser
```

Enable an advisory threshold on the package that would own the common API:

```yaml
architecture:
  packages:
    application-api:
      match: app.features.*.application
      min_imports_to_bundle: 3
```

IMP001 suggests the matching `application` package facade once one outside file
imports at least three distinct implementation modules there. Repeated imports,
multiple symbols from one module, and imports in separate callers do not inflate
the count. Imports from inside the package and symbols already exported by its
facade do not count. The threshold is configurable and disabled when omitted.

For overlapping `min_imports_to_bundle` and `min_importers_to_export` declarations matching the same
concrete package, the pattern with more literal segments wins. An exact package
match overrides a wildcard. Identical equally specific settings produce one
decision; conflicting equally specific settings are configuration errors.
These thresholds do not inherit into unmatched child packages.

After deliberately defining the exports, the caller can use a combined import
such as `from app.features.users.application import DeleteUser, GetUser, UpdateUser`.
Review dependency direction and cycles before adding exports. This is a `hint`
by default; it does not fail the usual error gate. Choose `public: ['.']` instead
to require facade-only imports regardless of their count. No facade or import is
rewritten automatically.

These package API checks also cover discovered tests and `conftest.py` under
`project.test_roots`, for example `backend/tests`. Tests are outside callers even
when their configured test root is inside the package matched by the policy. Thus
`public: ['.']` rejects deep test imports with [ARC007](rules/ARC007.md), and
`min_imports_to_bundle` reviews per-test-file fanout with [IMP001](rules/IMP001.md).
Production implementation imports inside the package remain allowed.

When unit tests deliberately exercise a package's internals, set
`test_access: mirrored` on its contract. A test whose `tests.mirror` path mirrors a
module inside the package then counts as inside it for ARC007 and IMP001, so
`tests/recording/test_timeline.py` may import `app.recording.capture`. Tests that
mirror another package, integration tests outside the mirror pattern and
`conftest.py` remain outside callers. The option applies per contract, so an
overlapping contract keeps its own `test_access`.

```yaml
architecture:
  packages:
    package-facades:
      match: app.*
      public: ['.']
      test_access: mirrored  # default public
```
`tests.quality_exclude` only affects test-quality reviews; use ordinary reasoned
suppressions for deliberate API exceptions. Project exclusions still apply.

When the exports already exist, enable `rules: {imports/prefer-package-import: hint}`
or `--select IMP004`. For example, if `app.observation.__init__` directly exports
`Observe` and `Snapshot`, it suggests replacing
`from app.observation.models import Snapshot` with a package import that can join
`from app.observation import Observe, Snapshot`. Every imported name must have a
direct, unconditional public re-export; aliases and relative imports are preserved
in suggestions. Tests use the same existing-export suggestions. Internal production
imports, package initializers and uncertain exports are excluded. See
[IMP004](rules/IMP004.md).

Third-party packages are checked only when `style.third_party_reexports` (or a
style override) lists their top-level import names:

```yaml
style:
  third_party_reexports: [fastapi, pydantic]
rules:
  imports/prefer-package-import: hint
```

`from fastapi.routing import APIRouter` then becomes `from fastapi import APIRouter`.
Smelt reads the listed packages from the interpreter it runs in, so install it in
the project environment (`uv run smelt`); a package it cannot find is skipped. It
parses their initializers without importing them. Stubs (`__init__.pyi`) win over
sources, star imports count when the target declares a static `__all__`, and names
imported under `TYPE_CHECKING` count when a module `__getattr__` serves them and
`__all__` lists them, as pydantic does. The listed package sources are part of
the cache key, so upgrading a dependency refreshes the findings.

To find names that are widely imported but missing from a facade, configure
[ARC008 `architecture/missing-facade-export`](rules/ARC008.md):

```yaml
architecture:
  packages:
    browser-api:
      match: app.browser
      min_importers_to_export: 3
      count_tests: false  # default; true also counts discovered test files
```

ARC008 counts distinct outside modules importing each public implementation name,
including mixed import statements that IMP004 cannot shorten yet. It reports one
hint per name and package, anchored at the definition, with source/test importer
lists, existing re-exports elsewhere and a concrete import suggestion. Repeated
imports and aliases in one caller count once. Static re-export chains are resolved
to the defining module. Imports already using a package facade do not count as
deep coupling. Only selected source and discovered tests are visible; excluded,
unselected and external consumers are not assessed.
Other package exports and explicit `__all__` re-exports in regular modules are
listed as context, without selecting a canonical import path.

Private names and `TYPE_CHECKING`-only imports never count, even when the global
import policy includes type-checking edges. Namespace packages without an analyzed
initializer, conflicting or uncertain bindings, star exports and dynamic facade
exports recognized through `__getattr__`, dynamic `__all__`, `exec` or namespace
inspection calls are skipped. Arbitrary runtime mutation is not inferred.
Static `__all__` is respected; an existing binding excluded
from it needs only an `__all__` change. A package API that restricts access to its own
facade does not receive export suggestions. `count_tests: true` requires
`min_importers_to_export`, whose minimum is two importers.

The cycle check tests the proposed facade-to-definition dependency against the
analyzed explicit runtime import graph and reports a deterministic return path.
`no-cycle-found` does **not** establish safe initialization: dynamic imports,
implicit initializer execution and import order are outside this check. Existing
cycles can also appear in the witness. Treat the result as a design review;
frequency alone does not establish API ownership. No source or configuration is
changed, and there is no automatic facade rewrite. Once exports are
deliberately added, IMP004 can suggest the corresponding caller imports, and
`public: ['.']` can enforce a chosen facade boundary through ARC007.

## Module headers and Python-version-aware annotations

Module docstrings and future imports can be governed independently:

```yaml
# Optional override of the minimum supported version, when metadata is absent:
project:
  python_version: "3.14"

style:
  overrides:
    source-conventions:
      match: app
      module_docstrings: forbid
      future_annotations: when-needed
```

Choose one canonical `module_docstrings` convention for a package:

- `require` makes LAY002 require a non-empty initial docstring in every
  analyzed production module, including empty files, `__init__.py` and `__main__.py`.
  Empty/whitespace-only strings, comments, later strings and class/function
  documentation do not satisfy the requirement.
- `forbid` makes LAY002 reject any initial module docstring, even an empty one.
  Class/function documentation and ordinary string values are unaffected.
- `allow` explicitly permits either style in a more specific package. Omitting
  the field inherits the closest explicit package policy, or adds no policy
  when none exists. Only analyzed source files are checked; separately discovered
  tests and excluded files are unaffected.

The single policy field keeps `require` and `forbid` mutually exclusive for each
module; a more specific package can deliberately choose the opposite convention.
For an individual file exception, mark the file with a reason:

```python
# smelt: ignore-file[LAY002] -- Empty compatibility facade; documented in the public API.
```

The same code covers retaining a docstring under `forbid`. Normal suppression
checks require reasons and flag unused exceptions. Smelt never adds or removes
documentation automatically.

`future_annotations` supports three modes:

- `allow`: preserve the import, including a deliberate runtime compatibility use.
- `forbid`: TYP001 rejects `from __future__ import annotations` at any version.
- `when-needed`: TYP001 rejects it for a declared minimum of Python 3.14 or newer.
  With an earlier or unknown minimum, TYP002 gives a conservative review hint for
  modules with no detected postponement need. It retains imports for unresolved
  or quoted forward references, `TYPE_CHECKING`, runtime annotation inspection,
  decorators, inherited models and unknown annotation expressions. Builtin
  generics and unions are considered with their supported Python version.

Version detection uses the nearest project's `requires-python` in
`pyproject.toml`, with an explicit `project.python_version` taking precedence.
The configured version means the **minimum supported version**. The interpreter
running Smelt and `.python-version` are not evidence that older Python releases
are unsupported. Monorepo members keep their own declared requirements; an
independent member with missing/invalid metadata stays unknown. Lower bounds are
validated with Python packaging specifiers; exclusions alone never raise the
inferred minimum. For external source roots, discovery also checks their import
root's parent; declare an override when the project boundary is otherwise unknown.

The report exposes versions, metadata provenance and uncertainty under
`coverage.python_targets`; context JSON includes `python_target` and the effective
`header_policy`, and findings explain which version was used.

Python 3.14 [defers annotations by default](https://docs.python.org/3.14/whatsnew/3.14.html#deferred-evaluation-of-annotations),
but the future import still changes their runtime representation. These are
source conventions and advisory evidence, not proof of equivalent runtime
behavior. Smelt never removes imports or documentation automatically. Verify
annotation consumers and tests before removing an import, and use an explicit
package exception where its string-based behavior is intentional.

Enable [LAY011](rules/LAY011.md) with `rules: {layout/misplaced-module-logger: warning}`
to place module-level `logging.getLogger(__name__)` assignments immediately after
imports and before constants or other code. Import-only guards such as
`if TYPE_CHECKING` can stay in the import section. Smelt reports the assignment
for manual review so initialization dependencies can be preserved.

## Definition order with execution dependencies

Enable `LAY004 layout/definition-order` to put the public interface first while keeping
prerequisites, binding versions and runtime boundaries intact:

```yaml
style:
  overrides:
    definition-order:
      match: app
      ordering:
        module:
        - docstring
        - future-imports
        - imports
        - dunder-assignments
        - type-vars
        - public
        - private
        - main-guard
        class:
        - class-attributes
        - init
        - dunders
        - public
        - private
        constants_exempt: false
        fix: safe-only
      rules:
        layout/definition-order: error
```

The defaults are shown above. Each list must contain its categories exactly once;
module docstrings, future imports and imports form the fixed prefix, and the
main guard belongs last. Use package `rules: {layout/definition-order: off}` to exempt a package.
The closest explicit rule setting wins. Enable the check in `rules` and
configure ordering in `style` or the matching `style.overrides` entry.

The analyzer partitions module and directly nested class bodies into sections.
Within each section it computes a stable topological order: configured priority,
with prerequisites inheriting the priority of their dependents. Relative order
inside a category stays unchanged. Functions may refer to helpers in their bodies
before those helpers are defined. Decorators, defaults, class creation,
assignments and eagerly evaluated annotations have different timing; existing
reads and later rebindings are preserved. Unknown Python minima are treated
conservatively as eager; the future import or a declared Python 3.14+ minimum
defers annotations, while defaults remain eager.

For a declared Python 3.10+ minimum, unions of recognized, unshadowed builtin
types and `None` in function annotations are modeled without an execution barrier.
For example, `str | bytes` does not pin an otherwise movable helper on Python
3.12/3.13. Imports from `builtins`, including aliases, are recognized; other
imported or user-defined types, subscriptions and unions in defaults remain
conservative execution boundaries. Shadowing and later rebindings still preserve
definition-time reads. Modules with explicit global/nonlocal mutation declarations
or detected writes to the `builtins` module retain conservative union barriers.

On Python 3.12/3.13, keep `from __future__ import annotations` when the chosen
public-first order uses forward references. TYP002's `when-needed` policy retains
the import in that case. Without it, eager annotation dependencies take priority
over public-first order; Smelt does not require inserting a future import just
to reorder definitions. `Self` is an alternative only when a method's return
contract follows the receiver's subclass, such as returning `self` or constructing
`cls`. A method that always constructs the named base class cannot promise `Self`.

Calls, unknown decorators, imports, control flow, `TYPE_CHECKING`, mutations,
operator/protocol evaluation and unsupported generic constructs are execution
barriers. Smelt keeps them in place. Literal construction through recognized
typing TypeVar/ParamSpec/TypeVarTuple factories has a dedicated category.
Consecutive overload/implementation and property getter/setter/deleter families
remain atomic. Noncontiguous property families are pinned. Function bodies,
including classes defined inside them, are outside this ordering analysis.

`constants_exempt: true` treats private uppercase module constants like public
definitions, allowing them to remain above the interface. Class attributes have
their own category. `__init__`, `__new__` and `__post_init__` share the initializer
category; other dunders precede ordinary public/private methods by default.
Names use the underscore convention; ordering does not infer semantic visibility
or rename symbols. If another tool changes visibility, run it before ordering.

Plain `smelt check` is always read-only. Request fixes explicitly:

```bash
smelt check --select LAY004 --format json
smelt check --select LAY004 --fix --format json
```

The fixer applies only visible, non-suppressed, non-debt sections within the
requested scope, then checks again. `fix: off` disables writes for a package.
Changed checks operate on affected reported sections, including any existing
ordering debt in a section that also contains a new inversion. Debt pruning and
changed comparisons track inversion identities, so adding a new inversion cannot
hide behind an existing aggregate finding.

Bounded fixes move source blocks, comments and their leading whitespace together;
they preserve UTF-8/BOM, newline convention and file permissions. Each section's
rewrite must equal its approved permutation. A section nested in another
reordered section is applied after the next check of the same `--fix` run.
Use the project's formatter afterwards to normalize spacing. Symlinks, external
source roots, concurrent edits, formatter/suppression regions, tab indentation,
runtime introspection and uncertain class/decorator behavior require manual
review. A misplaced main guard or import is diagnosed but never moved across an
execution barrier automatically. These bounds do not prove arbitrary Python
reflection or hidden runtime effects equivalent; verify types and behavior.

JSON includes expected/original order, semantic inversions, fixability and reasons.
`coverage.ordering` identifies the analyzed files and barriers; `context` exposes
the effective `ordering_policy`, including planned placement. A fixable finding
carries the move as a `safe` fix; `smelt check --diff` previews it. With `--fix`,
JSON also includes `fixes` with modified files and skipped fixes with reasons. Every other rule keeps its existing behavior; no visibility or module
rename fixer is implied by this option.

## Internal public functions, methods and static helpers

Adopt visibility and helper-placement conventions per package:

```yaml
style:
  overrides:
    internal-helpers:
      match: app
      rules:
        visibility/internal-public-member: warning
        layout/misplaced-static-helper: hint
    deliberate-api:
      match: app.integrations
      rules:
        visibility/internal-public-member: 'off'
        layout/misplaced-static-helper: 'off'
```

[VIS001](rules/VIS001.md) reviews public module functions and ordinary instance
methods with no observed use outside their defining module or class, including
unused definitions. For example, `emit` called or passed as `self.emit` only
inside `Browser` gets a private-name suggestion such as `_emit`. The JSON lists
internal reference lines and distinguishes unused members from internal helpers.
Existing private-name collisions get a distinct suggested name. Explicit module
exports, decorated members and inherited/subclassed classes are preserved.
Discovered test accesses count as external uses; VIS009 separately reviews
test-only public module functions when that named rule is enabled.

[LAY005](rules/LAY005.md) suggests moving an internal `@staticmethod` helper
to a private module function, such as `Browser._normalize(value)` becoming
`_normalize(value)`. It supports sync/async helpers and builtin decorator aliases;
class-namespace dependencies, inheritance, extra decorators and possible external
method accesses are excluded. Neither rule performs automatic changes.

Both named rules use the closest explicit package rule setting. They are advisory:
unknown same-named attribute access outside a class conservatively preserves its
methods, and external users or dynamic reflection cannot be ruled out. Review
the intended API before renaming or extracting a helper. `smelt context` includes
the effective policies even for planned files.

## Internal public module constants and type aliases

Enable [VIS002](rules/VIS002.md) separately to review module data:

```yaml
rules:
  visibility/internal-public-module-symbol: warning
```

The rule covers single, unconditional uppercase assignments and PEP 695
`type Alias = ...` declarations in implementation modules. It suggests reviewing
private names for data used only internally, including definitions with no observed
uses. Package initializers, static exports, imports and analyzed re-exports, test
accesses and escaped module values preserve visibility. Public signatures and
fields also preserve annotation dependencies, including quoted and transitive
type aliases: `Secret.value: SecretValue` is evidence for keeping `SecretValue`
public, not a reason to rename it automatically or require a facade export.

Rebound or shadowed names, star imports, dynamic export declarations and detected
namespace reflection are excluded. Ordinary assignment-style aliases and classes
are outside this check. Findings expose the selected roots, exclusions and
unassessed consumers through `usage_scope`; absence of observed use does not prove
an external API is unused. The closest package rule override applies. Review the
API before renaming; there is no automatic rewrite.
