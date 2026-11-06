# 05 — Approvals API ergonomics

- **Status:** proposed
- **Priority:** high
- **Breaking:** yes (parameter renames; `ApprovalGate` may change)
- **Depends on:** –

## Problem

### The most common configuration is the most verbose

`git grep -c "Approvals(default=PolicyDecision.ALLOW"` finds 129 occurrences
in 38 files. Tests, examples and docs all spell out "allow everything" the long
way:

```python
Agent(model, plugins=[Approvals(default=PolicyDecision.ALLOW)])
```

### Parameter names don't match the decisions they produce

```python
Approvals(
    default=PolicyDecision.ASK,
    trust=["read_file"],      # → PolicyDecision.ALLOW
    require=["shell"],        # → PolicyDecision.ASK
    deny=["rm"],              # → PolicyDecision.DENY
)
```

Only `deny` matches its `PolicyDecision` member. Readers have to learn that
`trust` means ALLOW and `require` means ASK.

### A tool author cannot mark a tool as "always confirm"

`@tool(readonly=True)` auto-allows. There is no opposite: a tool author can't
declare "this tool must always ask", so every application must remember to
list it under `require=`. `ToolAnnotations.destructive_hint` is descriptive
only, and `core/tools/info.py` says annotations "never grant local execution or
approval permissions". That separation is right, so the policy flag has to live
on `@tool`, not in the annotations.

### `ApprovalGate` vs `Approvals`

`ApprovalGate(Plugin[ApprovalGateConfig], needs=(ToolRegistry,))` and
`Approvals(..., slot="approvals")` overlap. `ApprovalGate` gates external/MCP
tools and keeps working when the approvals slot is replaced. It is referenced in
`docs/guides/approvals.md`, `examples/19_approvals_and_priorities.py`,
`harnessed/plugins/__init__.py`, and tests under `tests/core/` and
`tests/plugins/test_mcp.py`. Users can't easily tell when they need which one.

## Target API

```python
from harnessed.plugins import Approvals

Approvals.allow_all()                       # replaces Approvals(default=PolicyDecision.ALLOW)
Approvals.ask_all()                         # explicit form of the default

Approvals(
    default=PolicyDecision.ASK,
    allow=["read_file", "list_dir"],        # was trust=
    ask=["shell"],                          # was require=
    deny=["rm"],
    approver=my_approver,
    timeout=60,
)
```

```python
class Deploy(Plugin):
    @tool(confirm=True)                     # always ASK, unless the app explicitly denies it
    async def deploy(self, env: str) -> str: ...

    @tool(readonly=True)                    # unchanged: auto-ALLOW
    async def status(self) -> str: ...
```

Precedence, highest first. Document it in `docs/guides/approvals.md` and test it:

1. `deny=` (application)
2. `allow=` / `ask=` (application), so the app can always override the tool author
3. `@tool(confirm=True)` → ASK
4. `@tool(readonly=True)` → ALLOW
5. `policy=` callable
6. `default=`

Decision 3 covers whether step 2 may *lower* `confirm=True` to ALLOW.

`@tool(readonly=True, confirm=True)` raises `TypeError` at decoration time.

## Scope

- `harnessed/core/approvals.py`: classmethods, renamed config fields,
  precedence for `confirm`.
- `harnessed/core/tools/decorator.py` and the tool info/definition model:
  `confirm: bool = False`, carried through `PreparedToolCall`.
- Replace all 129 `Approvals(default=PolicyDecision.ALLOW)` uses with
  `Approvals.allow_all()` using a scripted, reviewed replacement.
- Rename `trust=`/`require=` at every call site (`git grep -n "trust=\|require="`).
- `ApprovalGate`: see decision 1.
- Docs: `docs/guides/approvals.md`, `docs/reference/sdk.md`, examples.
- `Changelog.md`: breaking entry with an upgrade table (`trust` → `allow`,
  `require` → `ask`).

## Acceptance

- Baseline checks pass.
- `git grep -n "Approvals(default=PolicyDecision.ALLOW)"` returns nothing.
- `git grep -nw "trust\|require" harnessed/core/approvals.py` returns nothing.
- A parametrised precedence test covers every row of the precedence list.
- An MCP tool test confirms external tools are still gated after the change.

## Decisions

1. **`ApprovalGate`:**
   - (a) Keep it as is and document *when* to use it.
   - (b) Fold its behaviour into `Approvals` so external tools are gated by the same
     policy, and remove `ApprovalGate`.
   - (c) Rename it to say what it does (e.g. `ExternalToolApprovals`).

   The ticket recommends (b), if the slot-replacement case can be covered by
   `Approvals` itself. Otherwise choose (c).
2. Add `ask_all()`, or only `allow_all()`? Recommended: both, for symmetry.
3. May an application `allow=` override a tool's `confirm=True`? Recommended:
   yes, because the application owner has the final say. Log the override as a
   `ToolPolicyAppliedEvent` with its source.
