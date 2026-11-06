# 06 — Public session views instead of `harnessed.core`

- **Status:** proposed
- **Priority:** high
- **Breaking:** no (additive; examples switch to the new paths)
- **Depends on:** –

## Problem

AGENTS.md says that applications use the root SDK, `harnessed.plugins`,
`harnessed.models`, `harnessed.errors` and `harnessed.hooks`. Several examples
still have to reach into `harnessed.core` and into the DI container to answer
basic questions:

```python
# examples/06_explicit_registration.py
from harnessed.core.tools.registry import ToolRegistry

assert "temporary_echo" in {t.name for t in session.ctx[ToolRegistry].definitions}

# examples/11_temporal_needs.py
from harnessed.core.prompt.system_prompt import SystemPromptStore
from harnessed.core.tools.registry import ToolRegistry

ctx[SystemPromptStore].fixed(f"Current browser: {browser.name}")

# examples/22_builtin_tool_lifetimes.py
registry = session.ctx[ToolRegistry]

# examples/features/workspace.py
from harnessed.core.prompt.context import DynamicContext
```

There are two separate gaps:

1. **Inspection from application code:** "which tools are visible right now?"
   requires `session.ctx[ToolRegistry]`. `Session` has `messages`, `usage`,
   `events` and `children`, but no `tools`.
2. **Service types for plugin authors:** a plugin that declares
   `needs=(ToolRegistry, SystemPromptStore)` or injects `DynamicContext` has
   to import them from deep core paths that AGENTS.md treats as internal.

## Target API

### Read-only `session.tools`

```python
names = session.tools.names  # frozenset[str], currently visible
"temporary_echo" in session.tools  # bool
definition = session.tools["read_file"]  # ToolDefinition, KeyError if absent
for definition in session.tools:  # iterate visible definitions
    print(definition.name, definition.description)
```

- `session.tools` returns a `SessionTools` view object: `Mapping[str, ToolDefinition]`
  plus `.names`.
- It is read-only. Registration stays with plugins (`@tool`) and `Context.register()`.
- It reflects `RegistrationState.active`, so the visibility a model would see is
  the same visibility the app sees.

### A public facade for injectable services

```python
from harnessed.services import DynamicContext, SystemPromptStore, ToolRegistry


class BrowserTools(Plugin):
    needs = (Browser, ToolRegistry, SystemPromptStore)

    def setup(self, ctx: Context) -> None:
        ctx[SystemPromptStore].fixed(f"Current browser: {ctx[Browser].name}")
```

- `harnessed/services.py` uses explicit re-exports (AGENTS.md facade style) and
  lists only types that plugins legitimately inject: `ToolRegistry`,
  `SystemPromptStore`, `DynamicContext`, and anything else found while migrating
  examples.
- The root lazy SDK does not re-export them, so the root stays small.

### Architecture guard

```python
# tests/test_architecture.py
def test_examples_do_not_import_core() -> None:
    offenders = [p for p in Path("examples").rglob("*.py") if imports_module(p, "harnessed.core")]
    assert offenders == []
```

## Scope

- `harnessed/core/runtime/session.py`: `tools` property returning `SessionTools`.
- New `harnessed/core/tools/view.py` (or similar) for `SessionTools`.
- New `harnessed/services.py` facade, plus a `smelt.yaml` entry giving it the
  public facade role.
- Examples: `06_explicit_registration.py`, `11_temporal_needs.py`,
  `22_builtin_tool_lifetimes.py`, `features/workspace.py`.
- `tests/test_architecture.py`: the guard above.
- `docs/reference/sdk.md`: add `tools` to the Session table and document
  `harnessed.services`.
- AGENTS.md: add `harnessed.services` to the list of application-facing modules.

## Acceptance

- Baseline checks pass.
- `git grep -n "harnessed.core" examples/` returns nothing.
- `git grep -n "session.ctx\[" examples/` returns nothing for read-only
  inspection use cases (see decision 2).
- Unit tests for `SessionTools`: visibility follows registration and removal,
  and the `__contains__` / `__getitem__` / iteration semantics hold.

## Decisions

1. Facade name: `harnessed.services`, `harnessed.runtime`, or put them under
   `harnessed.plugins`? Recommended: `harnessed.services`, because the types are
   not plugins and `runtime` sounds internal.
2. Should `session.ctx` stay a documented application API? Once `session.tools`
   exists, the remaining example uses are plugin-style DI. Ticket 07 renames
   `ctx` anyway, and this ticket only removes the *need* for it in examples.
3. Add other read-only views now (for example `session.prompt_preview()`), or wait
   for demand? Recommended: wait.
