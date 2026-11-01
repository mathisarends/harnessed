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

State and dependencies remain available through teardown and Context-owned
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

The canonical content name is `Instructions`; related names are `InstructionRef`,
`InstructionProvider`, `@instructions` and `PluginInstructions`. The renderer
plugin is `PromptRenderer`. No transitional aliases are retained.

Every selected declaration must be explicitly placed or excluded, even when its
rendered text is empty, the prompt is a plain string, or no prompt was supplied.
Coverage, foreign references, overlaps and exclusions are checked before content
providers or file reads. `Unplaced.ERROR` is the default; `PluginInstructions()`
and `Unplaced.APPEND` remain explicit delegation choices. Class docstrings never
become model instructions. Normalized `PromptContribution` values remain the
agent boundary and `PromptComposition` owns validation and rendering.

## Kit fragments

`Kit(name, plugins, fragments={name: (recipe_or_child_kit, ...)})` defines optional
named groups without a new kit inheritance API. `kit.instructions` selects its
named `instructions` group or all resolved descendants by default. Other names
use `kit.fragment(name)`. Selector order owns internal ordering; the application
places the fragment as a unit.

Fragments use the composition resolver's winner and replacement provenance.
Removed selectors contribute nothing; slot overrides contribute the final winner,
including in nested kits. A fragment from the result of `without` belongs to that
new kit. References from an uninstalled kit fail. Individual instruction references
keep exact recipe identity and never follow replacements. Fragments accept future
declarations of selected recipes; individual references expose additions as
coverage errors. Fragment exclusions skip content providers too.

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
