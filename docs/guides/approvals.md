# Approval composition

The default `Approvals()` permits read-only tools and asks before side effects.
Use an enum for a different fallback and decorated method references for rules:

```python
from harnessed import Agent, ApprovalAnswer, Plugin, PolicyDecision, tool
from harnessed.events import ApprovalRequestedEvent
from harnessed.plugins import Approvals, Filesystem
from harnessed.testing import ScriptedModel, call


class WorkspaceTools(Plugin):
    @tool(readonly=True)
    async def count_lines(self, path: str) -> int:
        return 42


async def approve(request: ApprovalRequestedEvent) -> ApprovalAnswer:
    return ApprovalAnswer.ALLOW


filesystem = Filesystem(root=".")
agent = Agent(
    model=ScriptedModel([call(WorkspaceTools.count_lines, path="README.md"), "done"]),
    plugins=[
        filesystem,
        WorkspaceTools(),
        Approvals(
            default=PolicyDecision.ALLOW,
            ask=[WorkspaceTools.count_lines],
            approver=approve,
        ),
    ],
)
```

Rules are evaluated in this order:

| Rule | Decision |
| --- | --- |
| In `deny` | DENY, without asking |
| In `ask` | ASK, including explicitly allowed or read-only tools |
| In `allow` | ALLOW, including a tool marked `confirm=True` |
| `@tool(confirm=True)` | ASK |
| `@tool(readonly=True)` | ALLOW |
| Explicit `policy(call)` result | Its PolicyDecision |
| Remaining tools | `default`, initially PolicyDecision.ASK |

`Approvals(default=PolicyDecision.ALLOW)` or `Approvals(default=PolicyDecision.ASK)`
choose the fallback; both accept `approver=` and `timeout=`. The default controls
otherwise unmatched tools. Tool flags and explicit rules still take precedence:
`default=ASK` permits a tool marked `readonly=True` without asking. To ask for a
specific read-only tool too, include its reference in `ask=[...]`.
A tool author uses `@tool(confirm=True)` for actions that need confirmation;
combining `readonly=True` and `confirm=True` raises `TypeError` at decoration.
Application selectors have the final say, and the resulting `ToolPolicyAppliedEvent`
records a policy or human-approval source.

`allow`, `ask` and `deny` accept decorated class methods or methods bound to a
configured plugin instance. If a tool appears in both `ask` and `allow`, `ask`
wins. References retain renamed tool metadata, check the active declaration,
and fail session startup when their tool is missing. An instance reference must
belong to that mounted recipe. Explicit string names remain an escape hatch for
dynamic integrations; they cannot receive the same declaration checks.
Policy reads the tool flags captured for the scheduled call, even if a binding
is subsequently replaced. Descriptive MCP annotations never grant permission.

An approver is a sync/async callback receiving `ApprovalRequestedEvent`, or a structural
service with `async ask(request)`. Both receive the same immutable request data.
`ALLOW` approves this call, `DENY` declines it, and `ALWAYS` records a reusable
grant for that tool and policy scope. Grants apply only within the current live session.
`await session.approvals.revoke(request_id)` revokes a grant. Forks revoke grants
by default. Policy denial and user denial retain distinct provenance.

An active [UI stream](streaming.md) emits `events.ApprovalNeededEvent` with
`request_id`, `call` and `reason`. Respond through
`await session.approvals.answer(request_id, ApprovalAnswer.ALLOW)`. The call waits
for in-memory admission. Identical acknowledged responses are idempotent;
a different second response fails. Late or expired responses fail with
`ApprovalUnavailable`.

Without a callback or an active UI responder, ASK immediately produces an
unavailable tool result. `Approvals(timeout=...)` sets a finite positive deadline.
`run()`, `submit()` and raw subscriptions do not install a UI responder.

Independent hard guards can use `ToolApprovalGuard(risky=[WorkspaceTools.count_lines],
scope="workspace")` or the `ToolPolicy` hook. They remain active when their
optional approver disappears or the `approvals` slot is replaced. This guard can
protect local and external/MCP tools independently of ordinary application policy. `Service(Approver, CallbackApprover(callback))` is
available for advanced service composition and receives the same request data;
normal application composition passes `approver=callback` directly.

Approval grants belong to one live session. Importing a conversation snapshot
re-evaluates the configured policy; it does not inherit permissions or unanswered
approvals. Save approval audit events externally if historical inspection is needed.
