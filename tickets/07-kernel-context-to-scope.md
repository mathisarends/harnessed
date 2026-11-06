# 07 — Rename kernel `Context` to `Scope`

- **Status:** proposed
- **Priority:** medium
- **Breaking:** yes (public kernel facade name, root export, `session.ctx`)
- **Depends on:** 06 (removes most application-level `session.ctx` uses first)
- **Requires owner sign-off:** AGENTS.md currently says "Keep the facade's
  exports in `harnessed/kernel/__init__.py` stable". This ticket changes one of
  them on purpose, so AGENTS.md must change with it.

## Problem

"Context" means three different things in this SDK:

| Name | Meaning |
|------|---------|
| `harnessed.kernel.Context` (root export `harnessed.Context`) | DI container and lifetime scope for plugins |
| `DynamicContext`, `add_context`, `ContextMessageEvent`, `ContextValueChangedEvent` | Model-visible prompt context |
| `ToolContext` | Per-call handle passed to tools |

The plugin signature `def setup(self, ctx: Context)` sits next to
`session.add_context(...)` and `ctx[DynamicContext].set(...)`, which reads like
"the context's context". `git grep -l "ctx: Context"` finds 66 files.

The kernel object is a lifetime scope: it owns registrations, resolves
`Inject[T]`, and closes children. The name should say that.

## Target API

```python
from harnessed.kernel import Inject, Scope, hook, plugin

class Browser(Plugin):
    def setup(self, scope: Scope) -> None:
        scope.provide(Page, Page())
        scope.register(...)

session.scope[ToolRegistry]          # was session.ctx[ToolRegistry]; rare after ticket 06
```

- `harnessed.kernel.Scope` replaces `harnessed.kernel.Context`, with no alias
  (AGENTS.md: no transitional aliases).
- `Session.ctx` → `Session.scope`.
- Root SDK: drop `Context` from the root export. Plugin authors import kernel
  types from `harnessed.kernel`, as AGENTS.md already requires for plugins and
  examples. Update `harnessed/__init__.pyi` and the runtime export table together.
- Parameter naming convention: `scope: Scope`.

Unchanged: `ToolContext`, `DynamicContext`, `add_context`, and the context
events. After the rename, "context" means only model-visible context and the
tool call handle.

## Scope

- `harnessed/kernel/context.py` → `harnessed/kernel/scope.py`. Rename the class
  and update the kernel facade `__init__.py`.
- `harnessed/__init__.py` / `__init__.pyi`: remove `Context`, and keep the stub in
  sync with the runtime export table (there is a test for this).
- `harnessed/core/runtime/session.py`: `ctx` → `scope`.
- Mechanical rename across `harnessed/`, `examples/`, `tests/`, `docs/`.
  Scripted, then reviewed by hand for prose that means prompt context.
- AGENTS.md: update the sentences that name `Context` (facade list,
  "Use `Context.register()`", "private Context lifecycle checks") and the
  "keep facade exports stable" rule, to note this deliberate change.
- `docs/reference/sdk.md`, plugin guides, `docs/contributing/plugin-recipes.md`.
- `Changelog.md`: breaking entry with the upgrade note
  `Context` → `Scope`, `session.ctx` → `session.scope`, `ctx` → `scope` parameters.

## Steps

1. Land ticket 06 so examples no longer use `session.ctx` for inspection.
2. Rename the kernel class and module, then the facade and the root export.
3. Run the scripted rename of `ctx: Context` → `scope: Scope` and `ctx[` → `scope[`
   in the touched signatures, followed by ruff and ty.
4. Rename `Session.ctx`.
5. Update docs, AGENTS.md and the Changelog.

## Acceptance

- Baseline checks pass. The kernel stays standard-library only (smelt).
- `git grep -nw "Context" harnessed/kernel` returns nothing.
- `git grep -n "from harnessed import.*\bContext\b"` returns nothing.
- `git grep -n "\.ctx\b" harnessed examples docs` returns nothing.
- The root stub/runtime export test passes.

## Decisions

1. Approve breaking the "stable kernel facade exports" rule for this rename?
2. Name: `Scope` (recommended; it matches the lifetime semantics), `Container`
   (DI-flavoured but hides the lifetime role), or `Kernel`.
3. Rename the parameter in hook signatures and `Inject` docs too
   (recommended), or only the type?
