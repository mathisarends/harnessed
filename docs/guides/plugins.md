# Plugin composition and lifecycle

An Agent is a resolved plugin list. `model=` supplies `Service(Model, model)`;
`system_prompt=` supplies `Instructions`. The default runtime plugins are
`AgentLoop`, `ToolOutputLimit`, `Approvals` and `NoopTracer`.
Use their public imports from `harnessed.plugins`. The tool registry, system prompt,
token counter and context budget come from an internal plugin without a slot that
the Agent always installs first. A replacement `loop` therefore keeps the tools;
replace a single service with `Service(TokenCounter, counter)`.

## Replace a default

```python
from harnessed import Agent, SystemPrompt
from harnessed.plugins import AgentLoop, Filesystem, ToolOutputLimit
from harnessed.testing import ScriptedModel

filesystem = Filesystem(root=".")
agent = Agent(
    system_prompt=SystemPrompt(sections=(filesystem.usage,)),
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

## Publish library defaults with Kit

Import `Kit` from `harnessed` to offer an ordered bundle of defaults. A kit has
no setup, teardown, hooks, tools or prompt of its own. Its factory may accept
ordinary application configuration; the kit only captures its name and recipes.

```python
from pathlib import Path

from harnessed import Agent, Kit
from harnessed.plugins import AgentLoop, Compaction, Filesystem, Shell
from harnessed.testing import ScriptedModel


def project_kit(root: str | Path = ".") -> Kit:
    return Kit(
        "project",
        [
            Filesystem(root),
            Shell(root),
            AgentLoop(max_steps=30),
            Compaction(keep_steps=4),
        ],
    )


agent = Agent(
    model=ScriptedModel(["done"]),
    plugins=[
        project_kit().without(Shell),
        AgentLoop(max_steps=10),
        Compaction(keep_steps=8),
    ],
)
print(agent.describe())
```

The supplied `AgentLoop` and `Compaction` replace the kit recipes in their slots.
The excluded Shell never mounts. Library defaults also replace Harnessed's
built-in defaults; explicit agent inputs replace either. `model=` and
`system_prompt=` are direct inputs, so they override kit-provided
`Service(Model, ...)` and `Instructions(...)`. Supplying the same slot twice
directly, including through a keyword and `plugins`, remains an error.

### Local precedence and ordering

At each composition boundary, direct recipes override contributions exported by
child kits with the same slot. Two direct recipes in one slot raise
`PluginConflict`. Two child kits exporting the same slot also conflict unless
that boundary supplies a direct recipe to decide it. Messages identify the slot
and competing kit paths.

```python
research = Kit("research", [project_kit(), Compaction(keep_steps=6)])
agent = Agent(
    model=model,
    plugins=[research, Compaction(keep_steps=10)],
)
```

Inside `research`, the six-step recipe overrides the project default. At the
agent boundary, the ten-step recipe overrides the result. Each kit must resolve
its own internal conflicts: an override at a parent boundary does not repair an
invalid child kit. Sibling kits have equal precedence regardless of their
internal nesting; adding a wrapper kit never silently settles their conflict.
The built-in defaults are the final fallback after supplied scopes are resolved.

Recipes keep their configured order within each scope. A replacement occupies
the first inherited position for its slot, even if the direct override appears
before the kit. When overriding several sibling kits, the first contributing
kit determines that position. Recipes without a slot remain additive; neither
their Python class nor their `provides` declarations cause implicit replacement.
Namespaced slot strings such as `"my_library.observation"` avoid collisions with
other libraries. Independent implementations can opt into the same slot without
inheriting from each other.

### Exclude recipes

`kit.without(*types)` returns a new kit and recursively removes every recipe
matching a requested type or subclass. Every requested type must match at least
one remaining recipe, or it raises `ValueError`; non-type arguments raise
`TypeError`. Chained calls search the remaining recipes. An empty call returns
an equivalent kit. Inputs are captured as tuples, so generators can be reused
through the resulting kit; recipes themselves remain shared configuration
objects rather than deep copies.

Only that kit and its children are affected. Exclusion does not remove dependents,
install providers, or disable Harnessed's fallback plugins. For example, excluding
a library's `AgentLoop` restores the built-in loop unless another override is
supplied. Missing service requirements are still reported at session startup.

### Inspect the resolved composition

`agent.describe()` returns an immutable `CompositionDescription`, exported from
`harnessed`. It does not open a session or execute setup or session prompt
factories. Its fields are:

| Field | Meaning |
| --- | --- |
| `plugins` | Tuple of plugin descriptions in final configured order, including built-in runtime services |
| `removed` | Tuple of exclusions, each with a `plugin` description and `removed_by` kit path |

Every plugin description exposes `name`, `kit_path`, `origin`, `slot`, `needs`,
`provides` and `replaces`. The latter is a tuple of replaced plugin descriptions,
preserving earlier replacement histories. Direct agent inputs have an empty
`kit_path`; built-ins use `("harnessed",)`. Service declarations reflect the
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
configuration snapshot; `session.ctx.plugin_diagnostics()` remains the live view
of waiting plugins and missing services.

Composition completes before prompt contributions and event declarations are
collected. Excluded and replaced recipes contribute no guidance, context metadata,
events, hooks or tools. Automatic prompt sections follow the resolved plugin
order. Explicit section references remain tied to their original recipe: a
reference to an excluded or replaced recipe raises `ConfigError` instead of
silently following its replacement. Reference the selected recipe or use a
`PluginSections()` rest slot when appropriate.

Kits are accepted by `Agent(plugins=...)` and other kits, not by kernel
`Context.plugin()` / `install()` or live `session.use()`. Runtime dependency
injection, binding overlays and plugin lifetimes retain their existing semantics.
Run `uv run python -m examples.36_plugin_kits` for a complete offline example.

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

Constructor attributes remain shared inputs by default, including caller-owned
clients and locks. Ordinary mutable attributes created in a constructor are also
shared: use `PluginState` or initialize them in `setup` for isolation. The default
`create_instance() -> Self` makes a shallow copy. Override it to reconstruct custom
instances, preserving recipe configuration and returning a fresh, unmounted object
of the same class. Tools and hooks then bind to that instance; bound tool references
on the original recipe remain valid.

Allocate asynchronous resources in `setup` and register cleanup immediately with
`ctx.effect(resource.aclose)`, or enter an async context manager with
`await ctx.enter(...)`. Effects run even if setup fails. `teardown` runs only
after successful setup. State factories should create plain values, not resources
requiring cleanup. Tools, listeners and services already register their own
reversible effects. `PluginState` is also available from `harnessed.kernel` for
standalone kernel plugins.

Async-generator function plugins run setup before `yield` and teardown after it.
They compose services and hooks. Public tools use `Plugin` methods, as shown in
the [tool guide](tools.md).

## Dependencies and provided services

Declare the plugin's service requirements explicitly with `needs`, and use
`Inject` to bind those services to attributes or method parameters:

```python
from harnessed import Inject, Plugin, SessionLog


class Activity(Plugin, provides=True, needs=(SessionLog,)):
    log: Inject[SessionLog]
    label: Inject[str | None]

    async def setup(self) -> None:
        self.session_id = self.log.id
```

Class `Inject[T]` attributes resolve on the mount before `setup`. Injection does
not add `needs`: the explicit declaration defines the plugin's lifetime and makes
its composition requirements visible. Optional attributes receive `None` when the
service is absent. Their values are captured for that mount. Access on a recipe or
after disposal raises `RuntimeError`. Forward references resolve when the recipe
is first compiled for mounting.

`provides=True` registers the mounted instance under the declaring class before
setup. Subclasses inherit that key: a subclass of Activity still provides Activity.
Use `provides=SomeKey` for a different class, Protocol or named service key, or
`provides=False` to disable inherited publication. Removal and failed setup remove
the service automatically. Strict composition detects closed declared dependency
cycles before mounting; manual providers retain the runtime dependency check.

Use ordinary `self.x = ...` assignments in setup for values computed once per
mount, and `PluginState` for mutable runtime state. Keep constructor attributes as
shared recipe inputs; mutable containers belong in state or setup.

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
through injection or dynamically through `ctx[Database]`. Function plugins use
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

## Session startup validation

`Agent` requires its configured plugins to be active when opening a session.
Validation checks the lifetime requirements declared through `needs`; `Inject`
continues to bind values without adding requirements.
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
`Context.install()` continues to allow deferred services by default; its `strict`
option is unchanged. `ctx.plugin_diagnostics()` provides a read-only snapshot of
waiting plugins in its shared runtime after `await ctx.settle()`, resolving each
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

## Configuration models stay outside plugin classes

```python
from pydantic import Field
from harnessed import Plugin, PluginConfig


class FailureNudgeConfig(PluginConfig):
    threshold: int = Field(default=2, ge=1)


class FailureNudge(Plugin):
    Config = FailureNudgeConfig
    config: FailureNudgeConfig

    def __init__(self, threshold: int = 2) -> None:
        super().__init__(threshold=threshold)
```

The Config alias selects validation; the annotation types `self.config`. The
explicit constructor gives callers ordinary Python signatures and IDE completion.
All built-in configuration models follow this module-level pattern. Small plugins
can use a normal constructor without a separate configuration model. Allocate
mutable per-mount state with `PluginState` or in setup; constructors describe
reusable recipes.

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
