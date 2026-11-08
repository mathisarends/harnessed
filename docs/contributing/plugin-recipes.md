# Plugin recipes, dependencies and explicit instructions

Status: **implemented**, 2026-11-01. This records the decisions completing the
design handoff “Plugin-Rezepte, Dependencies und explizite Prompt-Komposition”.
The [SDK reference](../reference/sdk.md), [plugin guide](../guides/plugins.md)
and [prompt guide](../guides/context.md) describe the implemented public API.
The service-contract decisions below were updated on 2026-11-07 and retain the
message and request-hook API introduced alongside them.

## TL;DR

- Declare a plugin's required service once as `database = needs(Database)`.
  This both gates activation and binds a typed attribute before setup.
- Use `cache = observes(Cache)` for a stable service-or-`None` binding that does
  not block activation. Both kinds of binding rebuild the activation and its
  `PluginState` when their provider changes, and remain valid through cleanup.
- Tool parameters describe model input unless explicitly marked `Inject[T]`.
  The current invocation is always `ctx: Inject[ToolContext]`.
- Applications supply implementations with `Service(Contract, implementation)`
  and use `Agent.run()` or `agent.session()`. They need no container operations
  for this workflow. Inspect tool availability through `session.tools.explain()`.

## Developer-facing API and session workflow

The application's `model` and `database` below are already configured objects.
The plugin declares consumption; the application chooses the implementations.

```python
from typing import Protocol

from harnessed import Agent, Plugin, ToolContext, tool
from harnessed.kernel import Inject, needs, observes
from harnessed.plugins import Service


class Database(Protocol):
    async def lookup(self, key: str) -> str: ...


class Cache(Protocol):
    def get(self, key: str) -> str | None: ...


class Lookup(Plugin):
    database = needs(Database)
    cache = observes(Cache)

    @tool(readonly=True)
    async def lookup(self, key: str, *, ctx: Inject[ToolContext]) -> str:
        await ctx.progress(f"Looking up {key}")
        cached = self.cache.get(key) if self.cache is not None else None
        return cached if cached is not None else await self.database.lookup(key)


agent = Agent(model=model, plugins=[Service(Database, database), Lookup()])
async with agent.session() as session:
    result = await session.run("Look up customer 42.")
    explanation = session.tools.explain("lookup")
```

Only `key` appears in the model's argument schema. The cache may be absent. A
missing database prevents normal session startup with a named dependency error
before any model request. No extra `needs=(Database,)` declaration is necessary.
For a single operation, `await agent.run(...)` owns the session automatically.

Python service dependencies are separate from text sent to the model. Task input
uses ordinary messages; request-only background is added through `PrepareRequest`.
Plugins that need session services bind the restricted `SessionContext`, rather
than the application session. Feature-owned instruction state remains separate
from conversation history; the removed `DynamicContext` API is not restored.

## Rationale for the service contracts

`needs` names an activation prerequisite. Combining the prerequisite and the
attribute binding establishes one invariant: every active instance has that
service, with the same binding through teardown and owned cleanup. Requiring a
second declaration would let access and lifecycle drift apart. Additional
`needs=(Readiness,)` metadata expresses the same prerequisite without an attribute.

`observes` names a different lifecycle relationship, rather than contradicting a
requirement with `optional=True`. It permits absence and reacts to provider binding
changes; mutations inside the same service object do not restart the plugin.
Optionality does not suppress provider or setup failures. State that must survive
reactivation belongs outside activation-local `PluginState`.

`Inject` explicitly identifies a framework-supplied invocation parameter. An
unmarked `ToolContext` is rejected, including keyword-only parameters, so provenance
is visible in source and consistent with the tool schema. Deliberate tool and hook
`Inject[Service]` remains call-scoped. Inferring plugin lifetime requirements from
it would let adding one tool parameter restart unrelated tools and plugin state.
Missing required tool services hide that tool and are explained through
`session.tools`; missing plugin prerequisites are startup composition errors.

Higher-level lifecycle methods use service fields so `setup()` and `teardown()`
cannot accidentally resolve different providers. The standalone kernel retains
its general callable injection. `Service` publishes an existing implementation;
consumption does not construct or acquire it. Resource owners acquire resources
in setup and register cleanup immediately. A consumer cannot require a service
that it only plans to publish later in its own setup.

Lifecycle methods receive the concrete `PluginScope` view, with a fixed async
signature accepting one scope parameter. Composition and closure remain with the
framework's full kernel `Scope`. This follows the existing `SessionContext`
boundary: plugin code receives the operations it needs, without orchestration of
its own owner. See [plugin scope design](plugin-scope.md) for the rationale,
retained integration operations and migration examples.

This is an intentional breaking change: `dependency(T)` becomes `needs(T)`,
`dependency(T, optional=True)` becomes `observes(T)`, lifecycle service parameters
become fields, and implicit tool contexts gain an `Inject` marker. Compatibility
aliases would preserve competing forms, so the current API has one spelling per
contract. See the [migration notes](../releases.md#breaking-change-plugin-service-contracts-and-explicit-tool-injection).

## Recipe and activation ownership

The default `create_instance() -> Self` reconstructs the concrete class by replaying
the explicit arguments captured at its outermost constructor. It does not copy
arbitrary instance attributes or resources. Constructors are repeatable declaration
code. Explicit caller-owned inputs retain identity; constructor-created containers
and `PluginState` values are fresh for every activation. Custom factories can
define explicit sharing, as the observable testing fakes do. Configuration models
must be frozen; configuration replacement is rejected.

`Plugin[MyConfig]` declares the runtime schema and typed `self.config` once.
The base constructor requires `config=MyConfig(...)` for configured plugins;
configurationless plugins keep plain `Plugin`. No `Config` alias, repeated
annotation or arbitrary keyword-forwarding API remains. Runtime schema resolution
uses generic base declarations, including inherited substitutions. Typed feature
constructors may build and pass their own explicit model internally.

State and dependencies remain available through teardown and Scope-owned
cleanup, including partial setup failure and cancellation, then become invalid.
Prepared tool calls still retain the original registration; they never follow a
replacement. Direct recipe tool calls fail without activating anything.

## Dependency declaration

`needs(Service)` is a read-only typed descriptor. It binds before setup and
adds a continuous requirement without needing a second `needs` declaration.
`observes(key)` captures the visible service or `None` and
observes changes without blocking startup. Appearance, disappearance or provider
replacement ends the activation and rebuilds its instance and state.

Classes, Protocols and shared named `Key[T]` values use the same syntax. Ordinary
annotations have no injection behavior. Class-level `Inject` was removed;
call-scoped `Inject` remains independent. Extra instance `needs=` replaces extra
class requirements, while dependency fields and registration requirements always
remain. `PluginSpec` normalizes duplicate hard `needs` and nonblocking `observes`;
composition descriptions, diagnostics and activation consume that description.

Higher-level `Plugin` lifecycle methods reject `Inject` parameters at composition;
their services belong in fields. Kernel `PluginBase` and function plugins retain
lifecycle injection. Tool and hook `Inject[T]` remains call-scoped and never adds
lifetime requirements. Tool contexts require the explicit `Inject[ToolContext]`
marker, including keyword-only parameters. Tool availability explanations include
both callback and predicate requirements without inspecting dormant plugins.

## Instruction declaration and placement

Plugins return one complete content document through
`instructions(self) -> SystemPrompt`. Static text may live in module constants;
configuration helpers return actual content. Methods are pure and synchronous.
`Instructions` holds literal text or session-owned file/factory sources.

Agents collect plugin blocks by default; applications and packs keep collection
with `SystemPrompt.from_plugins(format=...)` and opt out with a constructor-built
`SystemPrompt`. Resolve pack nesting, removals and slot replacements before
calling selected providers once at the agent boundary. Preserve whole plugin
blocks in resolved list order, independently of setup order. `None` parts and
plugin-list entries are normalized away.

`PluginPack(prompt=...)` provides a default in the existing instructions slot. Parent
prompts override child defaults, and explicit `Agent(prompt=...)` overrides pack
defaults. `Agent(instructions="...")` prepends caller text without replacing the
strategy. Selected instruction overrides without explicit collection fail before
provider calls. Providers cannot return another collection strategy.

Content documents use strings, `Instructions` and nested `SystemPrompt` values.
The outer format controls all nested content. There are no descriptors, section
references, fragments, rest slots, exclusion registries or unplaced policies.
Override the complete plugin method to replace or omit its block. Runtime
`SectionDelivery` retains explicit ERROR, CONTEXT and IGNORE policies.

## Runtime mounts and tests

Runtime mounts retain explicit `section_delivery`: `CONTEXT` delivers
activation-owned instructions and removes them on teardown, while `IGNORE` skips
providers. The fixed system prompt is unchanged. Events remain independent typed
contracts; subscriptions add no producer dependencies, and codec lifetime remains
independent of active listeners.

`harnessed.testing.mount_plugin(recipe, services=(Service(Contract, fake),))`
provides a small typed activation fixture, including a tool registry and guaranteed
cleanup. It needs no model, agent or prompt evaluation. `PluginHandle.instance`
exposes the current activation for kernel consumers.
