# Approval response timeout decision

Status: implemented. This is the current API contract.

## TL;DR

Configure `Approvals(timeout=120)` once for every approval question in a session.
Use `context.approve(reason, timeout=30)` only for a deliberate exception. A local
value replaces the default and may be shorter or longer; omission and `None`
inherit it. `AgentLoop` owns tool execution settings. Approval waiting preserves
the asking task's remaining execution budget.

## Developer-facing API

```python
from harnessed import Agent, Plugin, ToolContext, tool
from harnessed.plugins import Approvals


class DeploymentTools(Plugin):
    @tool
    async def deploy(self, *, context: ToolContext) -> str:
        if not await context.approve("Deploy to production?", timeout=30):
            return "Deployment declined."
        # Perform the approved deployment here.
        return "Deployment approved."


agent = Agent(
    model=model,
    plugins=[
        DeploymentTools(),
        Approvals(
            allow=[DeploymentTools.deploy],
            approver=ask_user,
            timeout=120,
        ),
    ],
)
```

`model` and `ask_user` are application-supplied model and approval callback values.
The framework supplies the `ToolContext` parameter; its name `context` is ordinary
Python naming. `deploy` is the model-facing tool. `context.approve()` asks for
permission during that tool's execution; it is neither a tool nor a hook.
The explicit allow rule lets this tool own its confirmation instead of also
asking before entering the tool.

For an approval outside a tool, the existing lower-level API is
`await session.approvals.request(call, reason, timeout=30)`, with a `ToolCall`
identifying the action. It returns `ApprovalAnswer`; `context.approve()` returns
`True` for `ALLOW` or `ALWAYS`, and `False` for `DENY`.

## Two levels, one approval owner

| Level | API | Meaning |
| --- | --- | --- |
| Session default | `Approvals(timeout=120)` | Wait up to 120 seconds for each approval answer by default. |
| One question | `context.approve(reason, timeout=30)` or `session.approvals.request(call, reason, timeout=30)` | Replace the default for this question only. |

The default remains 300 seconds. All explicit values must be finite and positive.
`None` means inheritance, not unlimited waiting. `timeout=300` can therefore
extend one question beyond a 120-second default. This setting is a default, not
an application-enforced maximum. Outer application and subagent deadlines remain
authoritative.

Every request captures its effective timeout before recording its question.
The countdown starts when waiting for a responder. A plugin replacement affects
subsequent questions. Guards, tool-policy hooks, in-tool questions and direct
requests use the same typed approval configuration, regardless of whether the
answer arrives through a callback, service, hook or stream.

No responder means immediate unavailability. Expiration cancels the responder
and resolves the question as unavailable, without granting permission. Explicit
denial remains a separate outcome. Cached grants do not wait for an answer.
The [approval guide](../guides/approvals.md#response-timeouts) defines the public
behavior in detail.

## Rationale

Previously, `Approvals(timeout=...)` applied only to requests initiated by that
policy plugin. Guards, in-tool questions and direct SDK calls normally fell back
to `AgentLoop(approval_timeout=...)`. The same question therefore had a different
timeout depending on its internal call path. Examples and tests configured both
values to express one application intention.

Approval response time belongs to the approval feature. The existing `Approvals`
plugin is already where applications select policy and an answer callback.
Giving it one default for every approval keeps those choices discoverable
together. The runtime resolves and enforces the timeout centrally; callers do
not need service or loop knowledge.

A local exception stays at the question that needs it. An application can allow
more time for a release decision or less time for a transient action without
changing the next question. The two levels express default and exception, with
explicit precedence.

Execution time and human response time measure separate activities. Charging
approval waits against a tool's execution timeout could end a question after
30 seconds despite a configured 120-second approval response timeout. The tool
now resumes with its remaining execution budget after an answer. Repeated or
nested approval waits do not replenish that budget. Parallel tasks keep their
own budgets; inherited background tasks cannot suspend their parent's work.
Run cancellation and outer deadlines can still interrupt approval waiting.

Subagents retain isolated configurations. A forwarded question is bounded by
both the child's original response deadline and the parent's response timeout;
forwarding cannot extend the child's deadline.

## Alternatives considered

- Keeping both defaults, or renaming them, preserves the need to understand
  internal callers. There is one default now.
- Moving the timeout solely to `AgentLoop` puts an approval concern in loop
  configuration, separated from the callback and policy it governs.
- Treating the default as a maximum would make a longer per-question value
  misleading. This API explicitly permits a local replacement in either direction.
- Removing per-question overrides would prevent a question from expressing its
  own response requirements. They remain optional on the existing request APIs.
- Restarting the execution timeout after approval would grant extra execution
  time on every question. The original remaining budget is preserved instead.

## Migration

Replace `AgentLoop(approval_timeout=value)` with `Approvals(timeout=value)`.
Remove the loop argument when both were configured. The removed argument has
no compatibility alias. Existing explicit request overrides retain their meaning.
