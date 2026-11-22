# Plugin composition and lifecycle

`MyPlugin(...)` constructs a reusable recipe. Each mount creates a fresh active
instance: constructors describe configuration, `setup()` acquires resources, and
`PluginState` owns mutable per-activation data. Direct tool calls require an active
instance; use `mount_plugin()` in plugin tests. Post-construction recipe attribute
assignments are not replayed into activations.

Pass `name=` and typed `config=` to the SDK base constructor as needed. Declare
static tools and hooks on methods with `@tool` and `@hook`. Register dynamically
in `setup(scope)` so the activation owns cleanup. Use `field = needs(T)` for
a service the plugin reads throughout activation. A class-level `needs=(T,)`
can express an activation prerequisite without a corresponding field.
`observes(T)` tracks an optional service across restarts. `Inject[T]` on a
method parameter resolves a value at call time, such as the current `ToolContext`.

Class lifecycle methods have one signature: `async def setup(self, scope:
PluginScope) -> None` and `async def teardown(self, scope: PluginScope) -> None`.
Import `PluginScope` from `harnessed`. It is the resource and registration
view for this activation; the framework owns composition and closure. See the
[activation contract](#activation-scope-and-lifecycle-contract) below.

An agent mounts the `Plugin` instances in `plugins=` in the supplied order.
Choose a model with `model=`, supply text or a structured document through
`instructions=`, and configure execution with `loop=`. The application owns
`approvals=` and `context=`. Each session owns its registrations and resources.

## Configure the loop and add features

```python
from harnessed import Agent, AgentLoop, Approvals, Context
from harnessed.plugins import Filesystem, FilesystemAction, LocalWorkspace

agent = Agent(
    model=model,
    plugins=[LocalWorkspace(root="."), Filesystem()],
    approvals=Approvals(ask=[FilesystemAction.DELETE]),
    context=Context(max_tool_output_chars=16_000),
    loop=AgentLoop(max_steps=50, max_parallel_tools=4),
)
```

## Share compositions with ordinary functions

```python
from pathlib import Path

from harnessed import Plugin
from harnessed.plugins import Filesystem, LocalWorkspace, Shell


def workspace(root: Path, *, shell: bool = False) -> list[Plugin]:
    plugins: list[Plugin] = [LocalWorkspace(root=root), Filesystem()]
    if shell:
        plugins.append(Shell())
    return plugins
```

Use `Agent(model=model, plugins=workspace(root))`. Each list entry must be a
`Plugin` instance. Duplicate slots raise `PluginConflict`. Unslotted features
are additive. Use a slot when two implementations claim the same capability.

### Inspect the composition

`agent.describe()` shows the selected model, loop and declared plugins before
a session opens. Inspect `session.tools` for currently available tools.
The SDK collects each plugin's instructions in list order by default.
See [ordered instructions](context.md#plugin-instructions-are-collected-by-default)
and run `uv run python -m examples.plugin_authoring.01_explicit_composition` for an offline example.

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
It is per-mount memory: use a [session value](#session-values) for facts that
belong to the conversation.

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

## Session values

Declare conversation facts that must outlive a mount with
`SessionValue(schema, key=..., default=...)`. Typical values are task lists,
handover notes and working memory. A session value survives compaction, plugin
restarts, snapshots and session stores. Each write appends a
`plugin.value.changed` event, so the session log stays the single source of truth.

```python
from pydantic import BaseModel

from harnessed import Plugin, tool
from harnessed.sessions import SessionValue


class Todo(BaseModel):
    title: str
    done: bool = False


class Todos(Plugin):
    items = SessionValue(list[Todo], key="acme.todos", default=[])

    @tool
    async def add_todo(self, title: str) -> int:
        items = await self.items.update(lambda items: [*items, Todo(title=title)])
        return len(items)
```

`get()` returns a fresh copy of the committed value, or of the default before
the first write. Without `default=`, the value type includes `None`.
`await set(value)` commits a new value. Writing an equal value appends nothing.
`await update(change)` applies a synchronous function to a copy of the current
value and commits the result atomically. A callback or validation failure
leaves the current value intact. Values must round-trip through the declared
schema as JSON. Reading a stored value that no longer matches the schema
raises `ValueError`.

The key is the stored identity. Every instance of the plugin class shares it,
so give it a namespace you own and keep it stable: renaming the key or changing
the schema incompatibly orphans the values in stored sessions. Child sessions
start without values. Use `PluginState` instead for data that should reset with
each mount.

Async-generator function plugins run setup before `yield` and teardown after it.
They compose services and hooks. Public tools use `Plugin` methods, as shown in
the [tool guide](tools.md).

## Activation scope and lifecycle contract

`PluginScope` is a concrete view of an existing kernel owner. Setup and teardown
receive the same view. It shares that owner's service visibility, event bus and
cleanup stack; creating it does not create a new container or copy any services.
`scope` is the conventional parameter name. An async lifecycle method always
accepts this parameter, even when it only uses `self`.

An activation belongs to exactly one session. Every hook it receives comes from
that session, so `event.session` is the same session as an injected
`SessionContext`, and a hook needs no check for foreign sessions. A subagent runs
in its own session with its own activations, even when the parent and child
agents share a plugin recipe. A parent sees a child through subagent events.

```python
from typing import override

from harnessed import Plugin, PluginScope, needs
from harnessed.sessions.log import SessionLog


class BrowserPlugin(Plugin):
    log = needs(SessionLog)

    @override
    async def setup(self, scope: PluginScope) -> None:
        async def tab_changed(tab: Tab, settled: bool) -> None:
            await scope.dispatch(TabChanged(tab, settled))

        browser = await scope.enter(
            ChromeBrowser(
                self.options,
                emit=self.log.emit,
                on_tab_change=tab_changed,
            )
        )
        scope.provide(Browser, browser)
        scope.provide(BrowserEvents, browser.events)
```

The browser types and options above belong to the integrating library. This is
the shape of its setup method, with its constructor and event declarations
omitted. `ChromeBrowser` supplies an async context manager: `scope.enter()` opens
it now and owns its eventual exit. Merely constructing the reusable plugin must
not open the browser. A connection to an external browser can close its own
connection on exit while a locally launched browser can also stop its process;
that distinction belongs to the resource's context manager.

The lifecycle contract is:

1. The framework reconstructs a fresh instance and binds `needs`, `observes` and
   `PluginState` before setup. These bindings remain stable through cleanup.
2. Setup acquires resources and installs owned services, listeners and integration
   registrations. The activation can use its own services during setup. Other
   plugins cannot resolve its services or invoke its listeners until setup and
   all declared hooks, tools and registrations have succeeded.
3. Success makes the activation visible. Required dependency loss or replacement
   suspends it; its next activation gets a fresh instance, bindings, state and view.
4. Shutdown stops dependent plugins first and hides the stopping activation's
   registrations from normal callers. Teardown runs only after successful setup,
   with the activation's state and dependencies still bound.
5. Owned cleanup runs in reverse acquisition order, including when setup fails,
   is cancelled, or teardown raises. Every cleanup is attempted; a failing cleanup
   does not skip the remaining ones. State is released after owned cleanup.

The visibility rule is not an isolation transaction: an external callback or an
event explicitly dispatched during setup can still reach already active plugins.
Acquire first and register cleanup immediately; a resource which fails inside
its own acquisition must unwind that incomplete acquisition itself.

For a Stage-style API returning an undo callback, keep installation explicit:

```python
async def setup(self, scope: PluginScope) -> None:
    stop_presenting = self.browser.input.present(Stage())
    scope.effect(stop_presenting)

    renderer = self.renderer
    if renderer is not None:
        view = renderer.create()
        self.state.view = view
        scope.effect(view.close)
        scope.on(LiveEvent, self._render)
        scope.on(BrowserEvent, self._browser_event)
        scope.effect(self._stop_activities)
```

These are library-specific resource APIs, not additional Harnessed operations.
`present(Stage())` performs installation and returns its undo function;
`effect()` only retains that function. It does not execute an installer. On
shutdown the activity task stops before the view closes, and the Stage is removed
afterward. The same order applies if a later setup operation fails. A library
which already supplies an async context manager can use `scope.enter()` instead.

`PluginScope` exposes:

| Operation | Contract |
| --- | --- |
| `await scope.enter(manager)` | Enter an async resource now and own its exit. |
| `scope.effect(cleanup)` | Own a sync or async cleanup callback without invoking it now. |
| `scope.provide(key, value)` | Publish a service when the activation becomes visible. |
| `scope.on(Event, handler)` / `@scope.on(Event)` | Install an owned, typed listener. |
| `await scope.dispatch(event, default=...)` | Invoke typed kernel hooks, optionally with a terminal default. |
| `scope.register(install)` | Install an integration registration with owned visibility and cleanup. |
| `scope[key]` / `key in scope` | Explicit typed lookup for integration registries and shared caches. |
| `scope.owner_name` | A diagnostic name for the owner. |

`effect`, `provide`, `register` and direct `on` return an async `Disposer` which
can release the registration early. Repeated disposal and later owner cleanup
run its cleanup at most once. Prefer `needs(T)` and `observes(T)` for plugin
dependencies: an explicit lookup does not declare a lifetime requirement or
subscribe to provider replacement.

Plugins cannot install other plugins, extend composition, settle dependencies or
close the owner through this view. Applications and standalone kernel integrations
keep `Scope`, `session.scope` and function-plugin composition. This is an API
boundary for ordinary plugin code, not a sandbox for arbitrary Python execution.
Use `@override` from `typing` to make the inherited lifecycle contract visible and
let a type checker catch misspelled methods. Generator setup and `@lifecycle` are
not part of this API.

`scope.dispatch()` delivers kernel hooks. Session `emit()` validates declared
session events and either records durable events or publishes transient events.
Declare session event types in `Plugin.events`; publish them through the injected
`SessionLog` or `SessionContext`. A library's `BrowserEvents` service is a separate
explicit contract and its implementation determines its relationship to these
surfaces.

## Dependencies and provided services

Declare plugin services once with `needs` and `observes` from the public kernel facade:

```python
from harnessed import Plugin, PluginScope, needs, observes
from harnessed.sessions.log import SessionLog


class Activity(Plugin, provides=True):
    log = needs(SessionLog)
    label = observes(str)

    async def setup(self, scope: PluginScope) -> None:
        self.session_id = self.log.id
```

`needs(T)` is a typed, read-only descriptor. It both binds the service before
setup and adds its key to the normalized lifetime requirements. A missing required
service leaves the plugin waiting; provider loss or replacement ends the activation
and creates a fresh instance, bindings and state. Access on a recipe or after
cleanup raises `RuntimeError`. No class is instantiated or globally searched for.
Ordinary annotations create no dependencies. Class-level `Inject[T]` fields have
been removed; `Inject` remains available on callable parameters.

The field expresses one contract: this activation needs the service, and accesses
that stable binding through the attribute. Do not repeat the key in `needs=(...)`.
Services are provided explicitly; declaring a field never constructs a provider.
Declare lifecycle services as fields: `Plugin.setup()` and `teardown()` reject
`Inject` parameters before activation. Both accept one `scope: PluginScope`
parameter to acquire and own resources. Standalone kernel `PluginBase` and function
plugins retain their general callable injection support.

`observes(T)` does not block activation. It captures the visible
service or `None` for that activation. Appearance, disappearance or replacement
restarts the activation and resets its state. When a plugin in the same list
declares the service with `provides=`, it starts before its observers, whatever
their order, so a fixed composition starts each plugin once. A service published
only through `scope.provide()` is not declared, so its observers restart when it
appears. Optional keys appear under `observes`
in `agent.describe()` and `PluginSpec`; they are distinct from hard `needs`.
Mutating the same service object does not change its binding or restart the plugin.
Optionality permits absence; provider and setup failures still propagate.
Additional `needs=(Readiness,)` requirements remain continuous lifetime
requirements without an attribute value. Instance `needs=` replaces the extra
requirements, while field and registration requirements always remain included.
Duplicate keys normalize once; hard requirements take precedence over observations.
Use `needs(Key[T]("name"))` with a shared key to distinguish providers.

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

from harnessed import Plugin, Service, needs, tool


class Database(Protocol):
    async def lookup(self, key: str) -> str: ...


class Lookup(Plugin):
    database = needs(Database)

    @tool(readonly=True)
    async def lookup(self, key: str) -> str:
        return await self.database.lookup(key)
```

`Service(Database, implementation)` provides the structural dependency.
`database = needs(Database)` declares the plugin's temporal requirement: a missing
service leaves it waiting, removing the service tears it down, and replacing or
restoring it restarts the plugin. Tool and hook parameters may deliberately use
`Inject[T]` to resolve the current service on each invocation. That declaration
does not require the service for the plugin's lifetime or restart the plugin when
the provider changes. Prefer a field when the entire plugin needs the service.
Tool declarations contribute their own registration requirement for `ToolRegistry`.

`Inject[T | None]` is optional and returns None while the service is absent.
Optional injections do not suspend a plugin unless the service is also declared
in `needs`. A required injection without a matching service raises `MissingService`
when resolved; required tool injections instead hide only that tool while missing.
Use `session.tools.explain(name)` to inspect its missing services. Call-scoped
values such as `Inject[ToolContext]` are supplied for tool
execution and do not belong in plugin `needs`. Named `Key[T]` values distinguish
two implementations of the same contract. A narrow Protocol is the service
contract; third-party implementations need no Harnessed base class.

`Service(Contract, instance)` makes an existing object available; ordinary service
registration does not acquire or close that caller-owned resource. A resource-owning
plugin acquires its resource in `setup()` and registers cleanup on its scope.

`async with session.mount(plugin) as handle` mounts a capability for a bounded
context, removing it on normal exit, an exception or cancellation. Use
`handle = await session.mount(plugin)` when the application manages its lifetime
manually, then `await handle.aclose()` to remove it. The mount delivers declared instructions as scoped context by default.
Choose `SectionDelivery.IGNORE` to skip section evaluation. Runtime facts use `PrepareRequest` hooks;
closing its handle removes its value and temporal restarts refresh it.
See [system prompt composition](context.md) for explicit ordering and rendering.

## Test one activation without an agent

```python
from harnessed import Service
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
`session.mount()` retain their temporal lifecycle behavior. The standalone kernel's
`Scope.install()` continues to allow deferred services by default; its `strict`
option is unchanged. `scope.plugin_diagnostics()` provides a read-only snapshot of
waiting plugins in its shared runtime after `await scope.settle()`, resolving each
plugin's needs in its own mount scope.

## Observe the opening of a session

Register the observer when building the agent. Harnessed awaits
`SessionOpened` once per opening, after restoring state and validating plugins
and tools, before queued work starts or the caller enters the session block.
The hook carries the live `SessionContext` and one `time.monotonic()` reading
shared by all listeners. Opening the same conversation again gives it a fresh
timestamp. Plugins mounted later receive future lifecycle notifications only.

Reopening a `SessionState` or `SessionCheckpoint` repairs work that was
interrupted mid-turn before `SessionOpened`: unfinished tool calls, requests,
approvals and subagents are closed, and an open turn that was not paused ends
as `aborted`. The repair runs after every plugin is active, including plugins
that need `SessionLog` or `SessionContext`, so their hooks observe those
records. `SessionOpened` observers then see the repaired log, and `on_commit`
receives the repair records before the session block is entered. A hook that
raises during repair fails the opening like a raising `SessionOpened` hook. A
paused checkpoint keeps its open turn and stays paused.

```python
from harnessed import Agent, Plugin, hook
from harnessed.hooks import SessionOpened


class SessionClock(Plugin, provides=True):
    started: float

    @hook(SessionOpened)
    def record_start(self, event: SessionOpened) -> None:
        self.started = event.at


agent = Agent(model=model, plugins=[SessionClock()])
async with agent.session() as session:
    started = session.scope[SessionClock].started
```

## Separate a backend from its tools

Choose a provider for the execution environment and add the tools the agent may
use. The tool configuration is the same for local, sandbox and custom backends.

```python
from harnessed.plugins import Filesystem, LocalWorkspace, Shell, WorkspaceBackends

local = [LocalWorkspace(root=project), Filesystem(), Shell()]
isolated = [sandbox, Filesystem(), Shell()]
custom = [WorkspaceBackends(filesystem=storage), Filesystem()]
```

`LocalWorkspace` supplies a filesystem and a shell rooted at the same directory.
`Shell(cwd="nested")` runs commands in a subdirectory. Local commands run as the
host user. Use a [sandbox](sandboxes.md) for isolation. Configure local process
environment and output capture on `LocalWorkspace(env=..., max_output_bytes=...)`.
`Shell(timeout_seconds=..., max_timeout_seconds=...)` controls the tool's timeout schema.

`WorkspaceBackends` accepts an existing `filesystem=`, `shell=`, or both. The
caller owns those objects and their lifetime. A provider by itself publishes
services only. With default validation, requesting tools whose backend is
missing raises `PluginDependencyError` at session startup. At runtime, the tools
suspend when their provider disappears and reactivate when it returns. There is
no implicit host fallback. `BackgroundShell()` opts into a separate tool set
for interactive process control.

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
`Compaction(keep_last_steps=4)` and `Shell(timeout_seconds=30)`, and use this pattern internally.
Callers of a custom constructor follow that class's explicit signature.
Allocate mutable per-mount state with `PluginState` or in setup; constructors
describe reusable recipes and acquire no resources.

Closed loop choices use enums, including `harnessed.models.TokenLimitBehavior`:

```python
from harnessed import AgentLoop
from harnessed.models import TokenLimitBehavior

loop = AgentLoop(on_max_tokens=TokenLimitBehavior.CONTINUE)
```

Plugin mount state, including Rules repetition counters, starts fresh in each live
session. Append conversation facts as user messages and export SessionState;
arbitrary Python plugin objects are not serialized.

## Third-party method declarations

`harnessed.kernel.registration(recipe)` attaches a public `Registration` to a
callable without wrapping it or changing its signature. It composes with `@tool`,
`@hook` and other registrations in either decorator order:

```python
from harnessed.kernel import registration

# binding implements Registration.needs and Registration.mount(scope, callback).
decorated = registration(binding)(callback)
```

The kernel collects `recipe.needs` before activation. Each activation calls
`recipe.mount` with the method bound to its newly reconstructed plugin instance,
not the reusable recipe. Use the supplied `Scope.effect()` or `Scope.register()`
to own cleanup, including failed startup. Registration objects are reusable
declarations; per-activation resources belong to their supplied scope. The helper
copies declaration metadata, so extending a `functools.wraps` wrapper does not
change the original callable's registrations.
