# Work with subagents

`Subagents` adds `spawn`, `status`, `send`, `wait`, `cancel` and `delegate` tools
only when explicitly configured. Each task opens a fresh child session.
`spawn(agent, task)` returns a child ID immediately so the coordinator can continue;
`delegate(agent, task)` waits for the answer and returns a JSON receipt containing
the child session ID, status, text and usage. The feature uses the existing model,
tool, approval and persistence pipelines and requires no additional dependency.

## Configure specialists

```python
from harnessed import Agent, PluginOrder, SystemPrompt
from harnessed.plugins import Approvals, Subagents

reviewer = Agent(
    model="openai:gpt-4.1-mini",
    instructions="Review the supplied code and explain concrete findings.",
)
coordinator = Agent(
    prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    model="openai:gpt-4.1",
    plugins=[
        Subagents({"review": reviewer}, max_parallel=2, max_steps=8),
        Approvals(allow=[Subagents.delegate]),
    ],
)

# Inside an async function:
# async with coordinator.session() as session:
#     result = await session.run("Ask the reviewer to inspect this code: ...")
#     print(result.text)
#     for child in session.children:
#         print(child.id, child.agent, child.status, child.text)
#     print(session.usage, session.tree_usage)
```

Install the appropriate [provider extra](models.md) for provider-model strings.
The [offline example](examples.md#subagents) uses scripted models instead.

The task is the child's only inherited input. It does not receive the parent's
instructions, conversation, context values, tools, approval grants or attachments.
Include the information it needs in the task. Each specialist uses its own model,
instructions, plugins and session store. Scope isolation is not an operating
system sandbox: configured backends still determine access to shared resources.

The `Subagents.instructions()` block lists available names. Select
`SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED)` to collect it with other
plugin instructions. Runtime mounts use `section_delivery=SectionDelivery.CONTEXT`
to add their instructions to session context. Unknown names and empty
tasks return tool errors without opening children. Python tools keep their normal
names, so this plugin reserves all six names in the parent's tool registry.

## Background tasks and on-demand reports

Configure the same `Subagents` mapping for both execution styles. Trust
`Subagents.spawn` to permit starting background tasks, and separately trust
`Subagents.send` and `Subagents.cancel` if those controls should run without
approval. `status` and `wait` declare read-only behavior; explicit denial or
required approval rules still apply.

| Tool | Behavior |
| --- | --- |
| `spawn(agent, task)` | Admit a task and return a JSON snapshot containing `child_id` |
| `status(child_id, timeout=10.0)` | Ask for a fresh report and wait at most `timeout` seconds for the answer |
| `send(child_id, message)` | Acknowledge guidance for the child's next step; queued children receive it when started |
| `wait(child_id, timeout=30.0)` | Return the child's current snapshot, waiting up to `timeout` seconds for completion |
| `cancel(child_id)` | Cancel the child driver and drain its plugin cleanup before returning |
| `delegate(agent, task)` | Start a task and wait until its terminal receipt |

Tool IDs are scoped to this parent session. Unknown IDs return tool errors.
`status` and `wait` accept timeouts from 0 through 3600 seconds. Expiry or
cancellation of a status/result waiter does not cancel a spawned task. A finished
child returns its existing state without invoking its model; further guidance
requires a new task. Repeated `spawn` calls with the same call identity return
the existing child rather than launching another one.

`status` does not load a transcript for a separate summarization request. The child
answers using its normal next-step context. The supervisor keeps a bounded
runtime projection (`activity`, completed steps, active tools and last tool progress)
and submits a correlated request through the child's inbox. The child receives an
`answer_status(request_id, message)` tool through a child-only plugin. Its reply
answers that request without finishing the task. An in-flight model or tool call
finishes before the next child step handles the request; no second model driver is
opened. Status answers consume normal child steps and tokens and respect its limits.
The model may fail to answer; a report is a self-assessment, while runtime facts are
recorded independently.

```json
{
  "child_id": "child-123",
  "status": "running",
  "activity": "tool",
  "steps_completed": 3,
  "active_tools": ["read_file"],
  "report": {"request_id": "report-456", "state": "pending", "text": ""}
}
```

One pending report is shared by concurrent or repeated queries. On timeout it
remains `pending`; its eventual answer is delivered to the coordinator's inbox as
a `subagent.status` notification. A successful reply has state `answered`. If the
child finishes before answering, its report becomes `unavailable`. Text deltas and
reasoning are not treated as progress reports.

Spawned task completion sends a `subagent.finished` notification. Inbox delivery
is visible at the next coordinator step and wakes an idle coordinator. Keep an
explicit `async with coordinator.session()` scope open for background work and
use `session.subscribe()` to observe subsequent turns. `session.run()` and its
stream describe one turn; `Agent.run()` closes its session after that turn and
therefore cannot keep background work alive after returning. Completion notices
are bounded previews; use `wait` to retrieve the task result through normal tool
result review and output limits.

See the offline [background example](examples.md#background-subagents).

## Limits and concurrency

| Option | Default | Meaning |
| --- | --- | --- |
| `max_parallel` | `3` | Simultaneous child lifetimes for this parent mount |
| `max_calls` | `16` | Delegations admitted across the parent session, including previous mounts and stored history |
| `max_depth` | `1` | Maximum absolute depth, with the direct child at depth 1 |
| `timeout` | `180.0` | Seconds for a delegation, including its wait for a child slot, startup and execution; at most 3600 |
| `max_steps` | `8` | Maximum steps in each child turn |
| `token_budget` | `None` | Optional child-turn input plus output token budget |

Multiple calls in one model response can run concurrently, subject to
both the parent tool scheduler and `max_parallel`. `delegate` waits for completion;
`spawn` releases the coordinator after admission, including when its child is queued.
A delegated child failure returns a tool error and
does not cancel sibling delegations. Normal output limits and result review apply
to each delegation's tool result.

The delegation tool declares an explicit one-hour outer timeout so the ordinary
30-second tool timeout does not truncate specialist work. The configured timeout
usually expires first; cleanup is drained before the delegation returns.

The default `max_depth=1` permits direct children only: even a child configured with
a larger limit cannot start further children. An application may explicitly raise
the coordinator's limit to enable nesting. Nested delegation requires the specialist
to have its own `Subagents` plugin.
Each level enforces the smaller of its configured absolute depth limit and the
limit inherited from its parent. A child cannot raise an ancestor's depth limit.
Call and parallel limits are local to each parent, not a global tree semaphore.

Token budgets retain their [turn semantics](sessions.md#control-a-live-run): they
are checked before the next step and can be exceeded by an in-flight request.
The parent turn budget counts only parent model usage. `session.tree_usage` adds
recorded child receipts to the current runtime's local `session.usage`, each
receipt including its descendants without double counting. Receipts survive state
export and restoration; local model usage follows the existing runtime lifetime.
Running children and usage lost before a crash receipt can be missing; this is an
inspection total, not a historical billing ledger or a hard spending cap.

Tool resource scheduling remains session-local. Applications sharing writable
files or other mutable resources across children must coordinate those resources
in their shared backend or give children separate workspaces.

## Approvals and progress

The parent applies its normal policy to `delegate` and the other control tools. Trusting a start tool permits
delegation; the child's own tool policies still run. Every child approval request,
including explicit tool-local approvers, is routed through the parent responder.
During an owning parent turn, `session.stream()` delivers `ApprovalNeededEvent`, with the specialist
and child ID in the reason. Answer it through the parent session's
`session.approvals.answer(request_id, decision)`.

`SubagentApprovalRequestedEvent` links the forwarded call ID to the original child
request ID. Both logs retain their own approval records. Reusable `ALWAYS` grants
remain at the parent, scoped to the child session, original tool and original
scope. They do not authorize siblings; revoking the parent grant takes effect on
the child's next request. Child-local callbacks do not bypass this route. Without
a configured parent approver or an answering parent stream, approval is unavailable
and the guarded action does not execute. Spawned children can request approval
after the originating parent stream has ended; configure a session-wide parent
approver for that case. A finished stream is not an approval responder for later work.

Child `ToolProgressEvent` messages are forwarded as parent tool progress tagged
with the specialist and child ID. Child text and reasoning deltas stay in the
child session. Parent streams also expose durable lifecycle and approval-link
events; their `TurnResultEvent` still describes the parent turn.

## Ownership, persistence and recovery

The parent owns running children through the plugin's supervisor. Parent `abort()`,
session close, delegation timeout and plugin unmount cancel the child driver and
drain its plugin cleanup. Cancelling only a result waiter would leave the driver
running; the supervisor explicitly aborts it. Removing a runtime mount returns a
cancelled tool result so the parent's model history remains complete.

Parent `pause()` and `stop()` retain their normal step boundaries: the current
delegation finishes before either control acknowledges its boundary. `abort()`
interrupts it immediately, including background children while the parent is idle.
Spawned tasks are not awaited by a coordinator boundary; they remain owned until
completion or cancellation. Notifications follow the parent's normal inbox controls.
No child outlives the owning parent scope.

The parent records `SubagentStartedEvent` and `SubagentFinishedEvent`. The child
records `SubagentLinkedEvent` before starting work; inspect it through
`child_session.parent`. `session.children` returns immutable `ChildSession`
snapshots with IDs, originating turn/call IDs, status, answer, recorded usage and
`runtime` (`ChildRuntimeState`). `SubagentUpdatedEvent` records this bounded runtime
projection; `SubagentMessageSentEvent` records acknowledged guidance.
Snapshots are also available from `SessionLog.children` after replay.

Configure `Agent(store=...)` on each specialist if its state should survive the
session. For example, parent and child agents can share one
`JsonSessionStore(root=...)`; each uses its own ID and compare-and-set revision.
The default remains ephemeral. Applications own execution coordination and audit
retention through their stores; revision checks do not replace worker ownership.
Parent references do not override a specialist's retention policy, and deleting
a parent does not cascade-delete its children.

Child IDs derive from the parent session ID and delegation call ID. A repeated
call with the same specialist and task and a recorded terminal receipt returns
that receipt; it does not start a second child. Reusing its ID with different
arguments is rejected. After interruption, opening the parent marks an unfinished child
reference `interrupted` without reopening or replaying child tools. Inspect its
stored log and external effects before explicitly issuing a new delegation.
Child references, bounded runtime state and terminal receipts are retained in
`SessionState`, including running children at an idle parent boundary. Opening a
copied snapshot also marks these references interrupted; copying never clones live work.
A running delegation within a parent turn is represented in the checkpoint's
current-turn suffix. The parent
flushes its start record before opening the child and its receipt before returning
the tool result. Stores receive those changes with their checkpoint in a
`SessionCommit`. Recovery does not promise an atomic commit across parent and
child stores. Continuing or copying parent state retains historical child
references and receipts; it does not clone children.

## Custom session factories

`Agent` implements the small `ChildSessionFactory` protocol. Applications may
provide another implementation using `ChildSessionFactory` and
`ChildSessionRequest` from `harnessed.plugins.subagents`. Its `child_session(request)`
returns an asynchronous context manager yielding a `Session`. The factory must
honor the requested fresh ID, parent link, routed approver and optional status
reporter before starting
work, and own all session resources until the context exits. Plugins depend on
this core protocol rather than importing the application-level `Agent`.
