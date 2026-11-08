# Plugin scope API and rationale

`Plugin.setup(self, scope: PluginScope)` and `teardown(self, scope: PluginScope)`
receive a concrete resource and registration view. This is implemented API; the
[plugin guide](../guides/plugins.md#activation-scope-and-lifecycle-contract)
defines its behavior, and the [SDK reference](../reference/sdk.md#session-tool-inspection-and-plugin-services)
records the developer-facing surface.

## Problem and decision

A Chrome integration opens a connection, publishes `Browser` and `BrowserEvents`,
and forwards tab changes into kernel hooks. A Stage integration installs input
presentation, optionally opens a renderer, subscribes to events and stops an
activity task. Both need a resource owner. Neither needs to install other plugins,
reconcile dependency graphs or close the session's composition scope.

The full kernel `Scope` exposes all these operations in one completion list.
Calling that object `ctx` makes its role even less visible, but renaming the
parameter alone does not establish a smaller API. Adding a lifecycle decorator
would change the declaration without addressing the size of the supplied object.

The SDK therefore supplies `PluginScope`, a real view rather than a Protocol-only
annotation over the full object. It delegates to one existing owner; it does not
make a second container, event bus, cleanup stack or lifetime. The kernel still
owns activation, suspension, dependency ordering and disposal. The view is an API
boundary, not an isolation mechanism for arbitrary Python code.

`PluginScope` is exported by `harnessed.kernel`, alongside `Scope`, without adding
another root-SDK entrypoint. The standalone kernel remains standard-library-only.
Kernel `PluginBase` and function plugins retain their general composition and
injection capabilities. The application retains `session.scope`.

## Developer-facing API

```python
from typing import override

from harnessed import Plugin
from harnessed.kernel import PluginScope


class Feature(Plugin):
    @override
    async def setup(self, scope: PluginScope) -> None: ...

    @override
    async def teardown(self, scope: PluginScope) -> None: ...
```

Both methods are ordinary coroutines accepting exactly one positional scope
parameter. They receive the same view for one activation. An omitted scope,
generator method or extra lifecycle arguments fail during plugin normalization,
before setup begins. `@override` is the standard typing marker; it does not
register a second lifecycle system. An implementation may omit either method and
inherit its no-op implementation.

The view provides `enter`, `effect`, `provide`, `on`, `dispatch`, `register`,
`owner_name`, typed `scope[key]` and membership. Their meanings and ownership
match the existing kernel operations. The view has no `plugin`, `install`,
`extend`, `settle`, `aclose`, ambient activation control or legacy `ctx` alias.

Explicit lookup remains for integration registries and shared per-session caches.
Built-in shell tools register result converters on `ToolRegistry`, MCP registers
schema-defined tools, and skill instructions share a discovered catalog. Removing
lookup would require unrelated replacement mechanisms for these real consumers.
Ordinary plugin dependencies continue to use `needs(T)` and `observes(T)`, which
also establish lifetime relationships; lookup alone does not do that.

Owned integrations accept either a kernel `Scope` or a `PluginScope` as their
registration owner. `RegistrationState.active` controls visibility and
`RegistrationState.activate()` invokes callbacks in that owner's ambient scope.
Tool execution and enabled predicates retain the originating registration's
ownership without exposing the full scope through the plugin view.

## Resource syntax and lifecycle guarantees

The existing `scope.enter(manager)` is sufficient for Chrome's context-managed
connection. The Stage API returns an undo function, so its setup spells the two
operations separately: install the Stage, then retain its undo with `effect()`.
`effect()` performs no installation. Both examples are shown in the plugin guide
using the resource contracts supplied by the integrating library.

The framework already owns reverse-order cleanup, visibility after successful
activation, dependency-first shutdown and rollback after failed setup. These are
now documented together at the lifecycle entrypoint and tested through the view.
State and stable dependencies remain available through teardown and cleanup.
The visibility boundary does not roll back external I/O or suppress events that
setup explicitly sends to already active plugins.

This change keeps one class-plugin lifecycle form. An optional generator setup
would introduce a second return contract and require rules for its interaction
with `teardown()`, override typing, failure and cancellation. A new `@lifecycle`
decorator adds another spelling. Neither is needed to establish the ownership
boundary. Generator-based kernel function plugins remain supported.

## Session content factories

`Instructions.from_factory` also supplies a `PluginScope`, so a deferred content
callback does not regain composition or closure operations. Its owner is the
session during initial prompt rendering, or the activation for runtime-owned
prompt rendering. The view does not change the caller's existing lifetime.
`Instructions.render()` and `PromptComposition.render()` accept either owner
form; a full `Scope` is converted to its stable `plugin_scope` view at the factory
boundary. Literal content and files need no scope.

## Event responsibilities

`scope.on()` and `scope.dispatch()` are the kernel hook surface. An injected
`SessionLog` or `SessionContext` publishes declared session events through
`emit()`: durable events enter the log and transient events are live only.
`Plugin.events` declares the session event catalog. None of these responsibilities
are inferred from the name of a library-owned `BrowserEvents` service.

This PR preserves those contracts. Whether browserrig needs an additional event
service is a question for that library's implementation and consumers.

## Migration

1. Import `PluginScope` from `harnessed.kernel` for higher-level class-plugin
   lifecycle methods. Retain `Scope` for standalone composition and application
   session integrations.
2. Annotate setup and teardown with `scope: PluginScope`; add the parameter to
   previously parameterless methods. Keep service dependencies in fields.
3. Update deferred instruction factory annotations to `PluginScope`.
4. Replace lifecycle calls to full-scope orchestration with application-owned
   composition. Resource and registration operations keep their existing names.

There are no compatibility aliases or implicit generator lifecycle variants.
Built-in plugins and consumer examples use this same public API.
