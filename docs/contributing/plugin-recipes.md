# Plugin recipes, dependencies and explicit instructions

Status: **implemented**, 2026-11-01. This records the decisions completing the
design handoff “Plugin-Rezepte, Dependencies und explizite Prompt-Komposition”.
The [SDK reference](../reference/sdk.md), [plugin guide](../guides/plugins.md)
and [prompt guide](../guides/context.md) describe the implemented public API.

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

`dependency(Service)` is a read-only typed descriptor. It binds before setup and
adds a continuous requirement without needing a second `needs` declaration.
`dependency(key, optional=True)` captures the visible service or `None` and
observes changes without blocking startup. Appearance, disappearance or provider
replacement ends the activation and rebuilds its instance and state.

Classes, Protocols and shared named `Key[T]` values use the same syntax. Ordinary
annotations have no injection behavior. Class-level `Inject` was removed;
call-scoped `Inject` remains independent. Extra instance `needs=` replaces extra
class requirements, while dependency fields and registration requirements always
remain. `PluginSpec` normalizes duplicate hard `needs` and nonblocking `observes`;
composition descriptions, diagnostics and activation consume that description.

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
