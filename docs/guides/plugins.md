# Plugin composition and lifecycle

An Agent is a resolved plugin list. `model=` supplies `Service(Model, model)`;
`prompt=` configures the `PromptRenderer` plugin; `instructions=` prepends caller text. The default runtime plugins are
`AgentLoop`, `ToolOutputLimit`, `Approvals` and `NoopTracer`.
Use their public imports from `harnessed.plugins`. The tool registry, system prompt,
token counter and context budget come from an internal plugin without a slot that
the Agent always installs first. A replacement `loop` therefore keeps the tools;
replace a single service with `Service(TokenCounter, counter)`.

## Replace a default

```python
from harnessed import Agent, PluginOrder, SystemPrompt
from harnessed.plugins import AgentLoop, Filesystem, ToolOutputLimit
from harnessed.testing import ScriptedModel

filesystem = Filesystem(root=".")
agent = Agent(
    prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    model=ScriptedModel(["done"]),
    plugins=[
        AgentLoop(max_steps=50, max_parallel_tools=4),
        ToolOutputLimit(max_chars=16_000),
        filesystem,
    ],
)
print(agent.plugins)
```

An explicit slot replaces the default in that slot. Two explicit plugins with
the same slot raise `PluginConflict`. A custom plugin declares its slot with
`class CustomLoop(Plugin, slot="loop")`. No hidden preset adds capabilities.

## Publish library defaults with PluginPack

Import `PluginPack` from `harnessed` to publish a named, reusable composition of
configured plugins and application defaults. A plugin pack captures its name,
ordered recipes and an optional explicit prompt strategy. It has no setup,
teardown, hooks or tools of its own. Its factory may accept ordinary application
configuration.

```python
from pathlib import Path

from harnessed import Agent, PluginPack, PluginOrder, SystemPrompt
from harnessed.plugins import AgentLoop, Compaction, Filesystem, Shell
from harnessed.testing import ScriptedModel


def project_pack(root: str | Path = ".") -> PluginPack:
    return PluginPack(
        "project",
        [
            Filesystem(root),
            Shell(root),
            AgentLoop(max_steps=30),
            Compaction(keep_steps=4),
        ],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )


project = project_pack().without(Shell)
agent = Agent(
    model=ScriptedModel(["done"]),
    instructions="Help with the workspace.",
    plugins=[
        project,
        AgentLoop(max_steps=10),
        Compaction(keep_steps=8),
    ],
)
print(agent.describe())
```

The supplied `AgentLoop` and `Compaction` replace the pack recipes in their slots.
The excluded Shell never mounts. Library defaults also replace Harnessed's
built-in defaults; explicit agent inputs replace either. `model=` and
`prompt=` are direct inputs, so they override pack-provided
`Service(Model, ...)` and `PromptRenderer(...)`. Supplying the same slot twice
directly, including through a keyword and `plugins`, remains an error.

### Local precedence and ordering

At each composition boundary, direct recipes override contributions exported by
child packs with the same slot. Two direct recipes in one slot raise
`PluginConflict`. Two child packs exporting the same slot also conflict unless
that boundary supplies a direct recipe to decide it. Messages identify the slot
and competing pack paths.

```python
from harnessed import PluginOrder

research = PluginPack("research", [project_pack(), Compaction(keep_steps=6)])
agent = Agent(
    model=model,
    plugins=[research, Compaction(keep_steps=10)],
    prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
)
```

Inside `research`, the six-step recipe overrides the project default. At the
agent boundary, the ten-step recipe overrides the result. Each pack must resolve
its own internal conflicts: an override at a parent boundary does not repair an
invalid child pack. Sibling packs have equal precedence regardless of their
internal nesting; adding a wrapper pack never silently settles their conflict.
The built-in defaults are the final fallback after supplied scopes are resolved.

Recipes keep their configured order within each scope. A replacement occupies
the first inherited position for its slot, even if the direct override appears
before the pack. When overriding several sibling packs, the first contributing
pack determines that position. Recipes without a slot remain additive; neither
their Python class nor their `provides` declarations cause implicit replacement.
Namespaced slot strings such as `"my_library.observation"` avoid collisions with
other libraries. Independent implementations can opt into the same slot without
inheriting from each other.

### Exclude recipes

`pack.without(*types)` returns a new pack and recursively removes every recipe
matching a requested type or subclass. Every requested type must match at least
one remaining recipe, or it raises `ValueError`; non-type arguments raise
`TypeError`. Chained calls search the remaining recipes. An empty call returns
an equivalent pack. Inputs are captured as tuples, so generators can be reused
through the resulting pack; recipes themselves remain shared configuration
objects rather than deep copies.

Only that pack and its children are affected. Exclusion does not remove dependents,
install providers, or disable Harnessed's fallback plugins. For example, excluding
a library's `AgentLoop` restores the built-in loop unless another override is
supplied. Missing service requirements are still reported at session startup.

### Resolve a pack into flat recipes

`pack.resolve(*, prompt=PluginPackPromptPolicy.INCLUDE)` returns a frozen `ResolvedPluginPack`
with `plugins` and `description`. Import both public types and `PluginPackPromptPolicy`
from `harnessed`. Resolution applies the same nested slot, ordering and exclusion
rules as Agent composition. It adds no agent defaults, mounts no plugins and
does not evaluate instruction providers or render prompt files.

```python
from harnessed import PluginPackPromptPolicy

resolved = project_pack().resolve(prompt=PluginPackPromptPolicy.OMIT)
plugins = resolved.plugins
print(resolved.description)
```

`INCLUDE` keeps each `PluginPack.prompt` as a prompt-renderer recipe, including its
normal slot conflicts. `OMIT` skips those declarations recursively before slot
resolution. Explicit `PromptRenderer(...)` recipes remain present. Selected
plugins retain their own instructions, descriptions, tools and event declarations;
the embedding application's prompt strategy determines how instructions render.
The source pack is unchanged.

`resolved.description` retains the original pack paths, replacement histories and
exclusions. Passing `resolved.plugins` to an Agent supplies a flat list of direct
inputs: the new Agent has no original pack scope, and its descriptions reflect
that. Pass the original pack when its precedence and provenance should remain
part of the Agent composition. A `ResolvedPluginPack` itself is an inspection result,
not a mountable recipe or an Agent plugin input.

### Inspect the resolved composition

`agent.describe()` returns an immutable `CompositionDescription`, exported from
`harnessed`. It does not open a session or execute setup or session prompt
factories. Its fields are:

| Field | Meaning |
| --- | --- |
| `plugins` | Tuple of plugin descriptions in final configured order, including built-in runtime services |
| `removed` | Tuple of exclusions, each with a `plugin` description and `removed_by` pack path |

Every plugin description exposes `name`, `pack_path`, `origin`, `slot`, `needs`,
`observes`, `provides` and `replaces`. The latter is a tuple of replaced plugin descriptions,
preserving earlier replacement histories. Direct agent inputs have an empty
`pack_path`; built-ins use `("harnessed",)`. Service declarations reflect the
recipe's metadata, not services dynamically published during setup.

```python
description = agent.describe()
for item in description.plugins:
    print(item.origin, item.slot)
    for previous in item.replaces:
        print("  replaces", previous.origin)
for removal in description.removed:
    print("removed", removal.plugin.origin, "by", removal.removed_by)
```

`print(description)` provides a readable view of the same data. This is a static
configuration snapshot; `session.scope.plugin_diagnostics()` remains the live view
of waiting plugins and missing services.

Composition completes before prompt contributions and event declarations are
collected. Excluded and replaced recipes contribute no guidance, context metadata,
events, hooks or tools. `SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED)`
explicitly collects complete instruction blocks in that final order. A pack may
publish this strategy with `prompt=`. Caller `instructions=` are prepended;
explicit agent `prompt=` overrides pack defaults. A selected instruction override
without a collection strategy raises `ConfigError` before providers run.
See [ordered plugin instructions](context.md#select-plugin-order-explicitly).

Plugin packs are accepted by `Agent(plugins=...)` and other packs, not by kernel
`Scope.plugin()` / `install()` or live `session.use()`. Runtime dependency
injection, binding overlays and plugin lifetimes retain their existing semantics.
Run `uv run python -m examples.36_plugin_packs` for a complete offline example.

## Per-mount state and cleanup

The configuration object is a reusable recipe. Declare mutable runtime state with
`PluginState(factory)`: the synchronous, zero-argument factory runs once per mount,
before `setup`, and again after a dependency restart. Each mount owns its value.

```python
from harnessed import Plugin, PluginState, tool


class Counter(Plugin):
    counts = PluginState[dict[str, int]](dict)

    @tool(readonly=True)
    async def count(self, name: str) -> int:
        self.counts[name] = self.counts.get(name, 0) + 1
        return self.counts[name]
```

Instance access returns the typed value; assignment replaces that mount's value.
Accessing state on a recipe or after disposal raises `RuntimeError`. Inherited
fields work normally, and subclasses can override their factories. State stays
available throughout `teardown` and owned cleanup, then its references are released.
It is per-mount memory: import conversation facts through explicit SessionState
in `setup`, as the built-in `Rules` plugin does.

Custom constructors must call `super().__init__()` to validate plugin configuration.
The default `create_instance() -> Self` replays the original constructor with its
explicit positional and keyword inputs. A constructor must only validate and
describe capabilities; it may run again for every session and dependency restart.
Values created inside it are fresh. Explicitly passed objects, such as clients and
locks, retain their identity; Harnessed never deep-copies resources. Pass reusable
collections rather than one-shot iterators as constructor inputs.

Use `PluginState` for lifecycle-bound mutable data. Override `create_instance` only
when an application needs another explicit construction/sharing contract; return a
fresh, unmounted instance of exactly the same class. Assignments to arbitrary
recipe attributes after construction are not replayed. Bound tool and instruction
references on the original recipe remain declarative references. Direct tool calls
on recipes or disposed activations raise `RuntimeError`; use the active instance.
`PluginConfig` models are frozen, and `plugin.config` cannot be replaced.

Allocate asynchronous resources in `setup` and register cleanup immediately with
`scope.effect(resource.aclose)`, or enter an async context manager with
`await scope.enter(...)`. Effects run even if setup fails. `teardown` runs only
after successful setup. State factories should create plain values, not resources
requiring cleanup. Tools, listeners and services already register their own
reversible effects. `PluginState` is also available from `harnessed.kernel` for
standalone kernel plugins.

Async-generator function plugins run setup before `yield` and teardown after it.
They compose services and hooks. Public tools use `Plugin` methods, as shown in
the [tool guide](tools.md).

## Dependencies and provided services

Declare stable service bindings with `dependency` from the public kernel facade:

```python
from harnessed import Plugin, SessionLog
from harnessed.kernel import dependency


class Activity(Plugin, provides=True):
    log = dependency(SessionLog)
    label = dependency(str, optional=True)

    async def setup(self) -> None:
        self.session_id = self.log.id
```

`dependency(T)` is a typed, read-only descriptor. It both binds the service before
setup and adds its key to the normalized lifetime requirements. A missing required
service leaves the plugin waiting; provider loss or replacement ends the activation
and creates a fresh instance, bindings and state. Access on a recipe or after
cleanup raises `RuntimeError`. No class is instantiated or globally searched for.
Ordinary annotations create no dependencies. Class-level `Inject[T]` fields have
been removed; `Inject` remains available on callable parameters.

`dependency(T, optional=True)` does not block activation. It captures the visible
service or `None` for that activation. Appearance, disappearance or replacement
restarts the activation and resets its state. Optional keys appear under `observes`
in `agent.describe()` and `PluginSpec`; they are distinct from hard `needs`.
Additional `needs=(Readiness,)` requirements remain continuous lifetime
requirements without an attribute value. Instance `needs=` replaces the extra
requirements, while field and registration requirements always remain included.
Duplicate keys normalize once; hard requirements take precedence over observations.
Use `dependency(Key[T]("name"))` with a shared key to distinguish providers.

`provides=True` registers the mounted instance under the declaring class before
setup. Subclasses inherit that key: a subclass of Activity still provides Activity.
Use `provides=SomeKey` for a different class, Protocol or named service key, or
`provides=False` to disable inherited publication. Removal and failed setup remove
the service automatically. Strict composition detects closed declared dependency
cycles before mounting; manual providers retain the runtime dependency check.

Use ordinary `self.x = ...` assignments in setup for values computed once per
mount, and `PluginState` for mutable runtime state. Keep explicit constructor inputs reusable; lifecycle-bound containers belong in
state or setup.

```python
from typing import Protocol

from harnessed import Inject, Plugin, tool
from harnessed.plugins import Service


class Database(Protocol):
    async def lookup(self, key: str) -> str: ...


class Lookup(Plugin, needs=(Database,)):
    @tool(readonly=True)
    async def lookup(self, key: str, *, db: Inject[Database]) -> str:
        return await db.lookup(key)
```

`Service(Database, implementation)` provides the structural dependency.
`needs=(Database,)` declares the plugin's temporal requirement: a missing service
leaves it waiting, removing the service tears it down, and replacing or restoring
it restarts the plugin. `Inject[T]` resolves an argument from the current scope
for each invocation; it does not infer lifetime requirements from class, setup,
hook or tool annotations. Declare `needs` for required services whether accessed
through injection or dynamically through `scope[Database]`. Function plugins use
`@plugin(needs=(Database,))`; bundles use `Bundle("name", needs=(Database,))`.
Tool declarations contribute their own registration requirement for `ToolRegistry`.

`Inject[T | None]` is optional and returns None while the service is absent.
Optional injections do not suspend a plugin unless the service is also declared
in `needs`. A required injection without a matching service raises `MissingService`
when resolved. Call-scoped values such as `ToolContext` are supplied for tool
execution and do not belong in plugin `needs`. Named `Key[T]` values distinguish
two implementations of the same contract. A narrow Protocol is the service
contract; third-party implementations need no Harnessed base class.

`await session.use(plugin)` mounts a capability at runtime. Plugins declaring
system sections are rejected before setup by default. Explicitly select
`section_delivery=SectionDelivery.CONTEXT` for mount-owned guidance or
`SectionDelivery.IGNORE` to skip section evaluation. Runtime facts use `ContextValue`;
closing its handle removes its value and temporal restarts refresh it.
See [system prompt composition](context.md) for explicit ordering and rendering.

## Test one activation without an agent

```python
from harnessed.plugins import Service
from harnessed.testing import mount_plugin

async with mount_plugin(Activity(), services=(Service(SessionLog, fake_log),)) as active:
    assert active.log is fake_log
    assert active.session_id == fake_log.id
```

`mount_plugin(recipe, services=...)` yields the concrete typed instance, supplies a
tool registry, checks required services, and guarantees cleanup even if the body
raises or is cancelled. It needs no model or agent and does not render instruction
providers. `PluginHandle.instance` also exposes the current activation, or `None`
while waiting or after disposal. Runtime fields remain available during teardown
and registered cleanup; they become invalid afterward.

## Session startup validation

`Agent` requires its configured plugins to be active when opening a session.
Validation checks the same normalized hard requirements used by activation and
inspection: dependency fields, extra `needs` and registration requirements.
Optional observations never block startup. Callable `Inject` parameters do not
add lifetime requirements.
After publishing `Session`, `SessionLog` and the other session services, it waits
for dependency-driven setup to settle and checks the full service scope, including
nested plugin installs. A waiting plugin raises `PluginDependencyError` before
the runtime starts, a model request is made or queued work resumes. Acquired
resources and the session lease are closed normally on failure. Persisted queued
the input state remains unchanged and can be supplied after correcting the composition.

The error is a `ConfigError`, importable from `harnessed.errors`. Its `.issues`
tuple lists every waiting plugin, including transitively blocked plugins. Each
immutable issue exposes `.plugin` (the exact `PluginSpec`) and `.missing` (the
currently unavailable service keys). Messages name the plugins and services;
applications can inspect the keys directly instead of parsing the message.

For a composition that deliberately waits for services added after opening,
select the warning policy explicitly:

```python
from harnessed import Agent, PluginValidation

agent = Agent(
    model=model,
    plugins=plugins,
    plugin_validation=PluginValidation.WARN,
)
```

This emits a `PluginDependencyWarning` from `harnessed.errors` with the same
`.issues` data and retains waiting plugins for later activation. It does not
suppress setup failures. `PluginValidation.ERROR` is the default and also applies
to the session opened by `agent.run()`.

Validation guarantees startup readiness. Later service removal, restoration and
`session.use()` retain their temporal lifecycle behavior. The standalone kernel's
`Scope.install()` continues to allow deferred services by default; its `strict`
option is unchanged. `scope.plugin_diagnostics()` provides a read-only snapshot of
waiting plugins in its shared runtime after `await scope.settle()`, resolving each
plugin's needs in its own mount scope.

## Separate a backend from its tools

`Filesystem` and `Shell` own their backend and tools together. Use
`FilesystemTools` or `ShellTools` when a separate service owns the backend:

```python
from harnessed.plugins import Service, ShellTools
from harnessed.plugins.shell import LocalShell, ShellBackend

plugins = [Service(ShellBackend, LocalShell()), ShellTools(".")]
```

The tool plugin explicitly needs the backend. Removing its provider suspends the
tools, and a replacement provider restarts them. Injection resolves the current
backend for every call. `FilesystemTools` exposes the same bounded reading,
writing, discovery, editing and deletion tools as `Filesystem`. For shared isolated
backends, see [sandbox integrations](sandboxes.md).

`ShellTools` accepts `cwd`, `timeout` and `max_timeout`. Set `env` and
`max_output_bytes` on `LocalShell` or the selected sandbox instead. `Shell` can
configure these options for its owned local backend; when passing an explicit
`implementation`, configure that backend directly. Filesystem backends implement
`grep(query, *, pattern="**/*", limit=100) -> SearchResult` so remote services can
batch searches. `harnessed.plugins.filesystem.search_files` provides a fallback
for backends using separate glob, stat and read operations.

## Typed configuration without duplicate declarations

Declare a module-level frozen model and use it once as the plugin's type argument:

```python
from pydantic import Field

from harnessed import Plugin, PluginConfig


class FailureNudgeConfig(PluginConfig):
    threshold: int = Field(default=2, ge=1)


class FailureNudge(Plugin[FailureNudgeConfig]):
    def reached(self, failures: int) -> bool:
        return failures >= self.config.threshold


nudge = FailureNudge(config=FailureNudgeConfig(threshold=3))
```

`Plugin[FailureNudgeConfig]` declares both the runtime configuration model and the
type of `self.config`. There is no `Config = ...` alias or repeated attribute
annotation. Construct the validated model explicitly and pass it with `config=`;
unknown fields, invalid values, missing configuration and the wrong model fail
before activation. Static type checking sees the model's fields and rejects
missing or mismatched config arguments. `Plugin` accepts no arbitrary configuration
keywords. Generic intermediate bases and inherited specializations preserve the
selected model. Configuration is immutable and the same validated object is retained
across activations.

Plugins without application configuration remain plain:

```python
class Observer(Plugin):
    pass


observer = Observer()
```

A feature may expose its own typed convenience constructor. It builds one explicit
config object and passes it to the base; no second configuration path is added:

```python
class FailureNudge(Plugin[FailureNudgeConfig]):
    def __init__(self, threshold: int = 2) -> None:
        super().__init__(config=FailureNudgeConfig(threshold=threshold))
```

Built-in features retain their typed constructors, such as
`AgentLoop(max_steps=20)` and `Shell(root)`, and use this pattern internally.
Callers of a custom constructor follow that class's explicit signature.
Allocate mutable per-mount state with `PluginState` or in setup; constructors
describe reusable recipes and acquire no resources.

Closed loop choices use enums, including `harnessed.models.TokenLimitBehavior`:

```python
from harnessed.models import TokenLimitBehavior
from harnessed.plugins import AgentLoop

plugins = [
    AgentLoop(on_max_tokens=TokenLimitBehavior.CONTINUE),
]
```

Plugin mount state, including Rules repetition counters, starts fresh in each live
session. Persist conversation facts through named context and export SessionState;
arbitrary Python plugin objects are not serialized.
