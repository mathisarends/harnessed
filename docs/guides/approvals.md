# Configure tool approvals

An agent allows every tool by default. Put the application policy on
`Agent(approvals=...)`. A tool publisher can give an action a stable identifier
that survives changes to the Python method, its parameters or its model-facing name.

```python
from enum import StrEnum

from harnessed import Agent, Approvals, Plugin, tool


class DocumentAction(StrEnum):
    PUBLISH = "publish"


class Documents(Plugin):
    @tool(action=DocumentAction.PUBLISH)
    def publish_revision(self, title: str) -> str:
        return f"Published {title}"


agent = Agent(
    model=model,
    plugins=[Documents()],
    approvals=Approvals(allow=[DocumentAction.PUBLISH]),
)
```

| Application policy | Matching calls | Other calls |
| --- | --- | --- |
| Omitted or `Approvals()` | All run | All run |
| `Approvals(allow=[action])` | Run | Denied |
| `Approvals(allow=[])` | Empty allowlist | Denied |
| `Approvals(ask=[action])` | Ask | Run |
| `Approvals(deny=[action])` | Denied | Run |
| `Approvals(default=Decision.ASK)` | Ask about every call | Ask |

`default=` explicitly chooses the fallback. A custom `policy(call)` may decide
unmatched calls before that fallback. Explicit rules must agree whenever they
match the same call. A single action can cover several tools. Enum members of
different classes have separate identities even when their values coincide.
The SDK publishes `FilesystemAction`, `ShellAction` and `SubagentAction` in
`harnessed.plugins` for its built-in operations. Local one-off rules can reference a decorated method, for example
`ask=[Documents.publish_revision]`. String selectors are rejected.
For external tools bind wire names once at the adapter using `actions=`.
The runtime checks the captured registration when it executes the scheduled call.
`readonly=True` describes tool behavior and does not decide permission.

## Respond to a request

Supply a sync or async callback as `approver=`. Return `request.allow()` for
one call, `request.allow(remember=True)` for a reusable grant, or `request.deny()`.
A reusable grant belongs to its action and approval scope. A tool without an
action key has a grant scoped to that registration. To revoke a grant use
`await session.approvals.revoke(request_id)`.

An active [UI stream](streaming.md) emits `ApprovalNeededEvent`. Use
`await session.approvals.allow(request_id)` or `await session.approvals.deny(request_id)`.
A repeated identical response is idempotent. A contradictory response fails.
A question without a responder yields an unavailable result.
`run()`, `submit()` and raw subscriptions install no UI responder.

## Response timeouts

`Approvals(timeout=120)` sets the default approval response timeout for the whole
session, in seconds. It applies to tool-policy questions, questions inside a tool, direct SDK requests and forwarded subagent questions.
Callbacks, services, hooks and stream responders use the same timeout. The
default is 300 seconds; values must be finite and positive.

There are two configuration levels:

```python
# Application default for every approval question.
Approvals(approver=approve, timeout=120)

# Inside a running tool: inherit the application default.
await context.approve("Deploy to production?")

# Give this one question a different response timeout.
await context.approve("Deploy to production?", timeout=30)
```

`context` is the framework-supplied `Inject[ToolContext]` parameter of a tool method;
`approve()` is a helper on that context, not a separate model-facing tool or hook.
See the [complete tool example](tools.md#ask-for-approval-inside-a-tool).

The lower-level equivalent is
`await session.approvals.request(call, reason, timeout=30)`, where `call` is a
`ToolCall`. Omitting `timeout`, or passing `None`, inherits the configured default.
An explicit value replaces it for this question and may be shorter or longer.
For example, `timeout=300` allows five minutes even when the default is 120
seconds. The application default is not a maximum. Overrides do not change later
questions. Invalid overrides are rejected before recording a question, even when
a reusable grant exists.

The response timeout starts when the session waits for the answer. Each question captures its timeout before recording the request. An existing grant answers immediately without waiting.
Expiration raises `ApprovalUnavailable` and records an unavailable resolution;
it is distinct from an explicit `DENY`. Late answers cannot authorize the action.
Tool-policy and in-tool approval failures produce an unavailable tool result.

Time spent waiting for approval does not consume the asking task's tool execution
timeout. Execution resumes with its remaining budget after the answer, including
when a tool asks more than once. Concurrent tasks keep their own budgets; an
approval requested by a background task does not suspend work in its parent task.
Explicit run cancellation and outer application or subagent deadlines still
interrupt the wait. Forwarding a child's approval does not extend the child's
existing response deadline; the parent's response timeout can end it earlier.

Configure approval response timeouts on `Approvals`, and tool execution timeouts
on `AgentLoop(tool_timeout=...)` or `@tool(timeout_seconds=...)`.
The [API decision and rationale](../contributing/approval-timeouts.md) explain
these responsibilities.
