# Conversations, persistence and explicit state

`Agent` holds reusable configuration. `Agent.session()` opens a live conversation;
`SessionState` is an immutable value that your application can save and inject
into another session. Harnessed does not store anything itself. Sessions are in
memory, and the application decides where their state goes: it receives each
durable change through an `on_commit` handler and reopens a conversation by passing
the stored value back as `state=`.

## Receive commits

```python
from harnessed import Agent
from harnessed.sessions import SessionCheckpoint, SessionCommit


class ConversationCommits:
    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def __call__(self, commit: SessionCommit) -> None:
        await self._repository.save(commit.session_id, commit.checkpoint.to_dict())

    async def load(self, conversation_id: str) -> SessionCheckpoint:
        document = await self._repository.load(conversation_id)
        if document is None:
            raise KeyError(conversation_id)
        return SessionCheckpoint.from_dict(document)


commits = ConversationCommits(repository)
agent = Agent(model=model, instructions="Include source URLs.")

async with agent.session(id="voyager", on_commit=commits) as session:
    await session.run("Compare the Voyager missions.", max_steps=20)

# A later request or process continues the same conversation.
state = await commits.load("voyager")
async with agent.session(state=state, on_commit=commits) as session:
    await session.run("Explain their different trajectories.")
```

`on_commit` is any async callable that accepts a `SessionCommit`. Harnessed never
opens, closes or retries an application-owned repository, pool or transaction.
`id=` names a new session; without it the session gets a fresh ID. Every
checkpoint carries its `session_id`, so `state=checkpoint` reopens the session
under the same ID. Key your rows by `commit.session_id`. Passing a different
`id=` together with a checkpoint raises `ConfigError`. Without a handler, nothing
is delivered and `flush()` does nothing.

`session.checkpoint()` returns the same `SessionCheckpoint` on demand, also while a
turn is running or paused. `session.snapshot()` remains the smaller terminal
`SessionState` for idle conversations; both are accepted by `state=`.

### When commits arrive

Commits are delivered inline at durability barriers, and the session waits for the
handler before it continues: input admission/removal, model request intent, tool
execution intent and receipts, reviewed tool results, turn completion, controls,
approval changes, feature-owned instruction state, compaction and custom durable
events. Thus an awaited `run()` has delivered its final reply, and `pause()` waits
for the completed step's commit. Model and tool execution happens between commits,
so keep database transactions inside the handler and outside model calls.

The handler runs inside log admission: the session holds its admission lock until
the handler returns. It may read the session, for example `session.checkpoint()`,
`messages` or `events`, but it must not write to or flush its own session. A call
such as `flush()`, `emit()`, `append_message()` or `steer()` from the handler
raises `SessionStateError`, which fails the commit like any other handler error.
Harnessed cannot detect tasks that the handler spawns: such a task waits until the
handler returns, so awaiting it from the handler deadlocks the session.

By default a delivery may take as long as the handler needs, and cancelling a
waiter does not interrupt it: abort and close wait for the delivery in progress.
Pass `commit_timeout_seconds=` to `agent.session()` to bound each delivery. When
the deadline expires, the handler is cancelled and the commit fails with
`CommitTimeout` from `harnessed.errors`, following the failed-commit path below.
Subagent sessions share their parent's deadline. Choose a deadline that leaves
room for storing large checkpoints.

Other durable events, such as `ToolProgressEvent`, are collected and travel with
the next barrier, `await session.flush()` or close. Text/reasoning deltas and
tool-argument previews are `TransientEvent` values: they never enter a checkpoint
or `SessionCommit.changes`. The assembled assistant message, including a captured
interrupted partial reply, is recorded as one durable event. Custom `DurableEvent`
values are barriers and await a commit for each emitted event; declare a custom
`TransientEvent` for frequent UI-only progress.

Subagent sessions deliver their commits through their parent's handler, each
under its own `commit.session_id`. Store one row per session; a child's
`commit.checkpoint.state.parent.parent_session_id` links it to its parent:

```python
async def save(commit: SessionCommit) -> None:
    parent = commit.checkpoint.state.parent
    await repository.save(
        commit.session_id,
        commit.checkpoint.to_dict(),
        parent_id=parent.parent_session_id if parent is not None else None,
    )
```

### Store state and execution apart

A checkpoint has two parts. `checkpoint.state` is the conversation at its last
closed turn and holds the history, retained images and spill text.
`checkpoint.execution` is a `SessionExecution`: the open turn's events, queued
inputs, the control state and the resources added since that boundary. While a
turn runs, every barrier carries the same state, so rewriting it at each commit
repeats work that grows with the conversation.

`commit.state_changed` says whether `checkpoint.state` differs from the state of
the session's previous delivered commit. It is true for the first commit, at turn
end, after idle compaction and after idle changes such as `append_message()`, and
false for barriers inside a turn and for idle input admission. Write the state
only when it changed and the execution part at every commit, in one transaction:

```python
from harnessed.sessions import SessionCheckpoint, SessionCommit, SessionExecution, SessionState


async def save(commit: SessionCommit) -> None:
    async with repository.transaction() as tx:
        if commit.state_changed:
            await tx.save_state(commit.session_id, commit.checkpoint.state.to_dict())
        await tx.save_execution(commit.session_id, commit.checkpoint.execution.to_dict())


async def load(session_id: str) -> SessionCheckpoint:
    state, execution = await repository.load(session_id)
    return SessionCheckpoint.from_parts(
        SessionState.from_dict(state), SessionExecution.from_dict(execution)
    )
```

`SessionCheckpoint.from_parts()` rejects an execution part that was captured
with a different state, and `Agent.session(state=...)` validates the result like
any checkpoint. The execution part does not contain the images and spill text
retained in the state, and its size inside a turn depends on that turn, not on
the conversation before it. A recorded model request lists every message it
sent, so the execution part stores it as a `CheckpointRequest`: a message equal
to one in the state, or to one in an earlier request of the same turn, is stored
as its ID. A message that a `PrepareRequest` hook or compaction changed is stored
in full, so `from_parts()` rebuilds exactly the request the model received.
`checkpoint.events` always holds the complete `ModelRequestedEvent`.
`SessionExecution` format 6 includes a SHA-256 digest of the conversation state.
It detects stale state even when two snapshots have the same history position. See
[the runnable split-commit example](https://github.com/mathisarends/harnessed/blob/main/examples/applications/23_split_commits.py).

### When a commit fails

An exception raised by the handler, or `CommitTimeout` after an expired
`commit_timeout_seconds`, reaches the waiting `run()`,
`RunHandle.result()` or stream iterator. The session then rejects further
admissions with the same error, cancels active work for cleanup and does not
retry. Controls such as `abort()` that need a commit raise the same error, while
closing the session still completes. A failed input commit therefore prevents the
input from running, and a failed model or tool intent commit prevents its execution. Applications map these
exceptions to their HTTP, SSE, WebSocket or job-status API. Resolve the storage
failure and reopen from the last stored checkpoint; the uncommitted turn is lost
rather than half-applied.

A lost acknowledgement can leave the client uncertain even when the database
committed. Reopen and reconcile using application IDs rather than blindly
rerunning external actions. Harnessed does not coordinate concurrent writers:
serialize execution for each conversation through your job runner or ownership
mechanism, and use the repository's own revision or lock if two workers can race.

### Commit application changes atomically

Each delivery is one frozen, Pydantic-validated `SessionCommit`:

```python
checkpoint = commit.checkpoint
for change in commit.changes:
    position = change.sequence  # Conversation-local, zero-based event position.
    event = change.event  # Concrete built-in event or CheckpointExtension.
```

`changes` contains all new durable events since the previous delivered commit, in
order. Sequences are contiguous and end at `commit.next_sequence`, which is
`checkpoint.offset + len(checkpoint.events)`. They include events that compaction
removes from working history. Reopening restores the checkpoint without replaying
old changes; recovery repairs can generate new ones. Importing a terminal
`SessionState` does not synthesize historical changes.

To keep domain messages and recovery state consistent, map the changes inside
your handler and write them with the checkpoint in one application-owned
transaction:

```text
BEGIN
  Insert or upsert mapped message/domain rows
  Upsert the checkpoint
COMMIT
```

Return only after commit. If the transaction raises, both writes roll back and
the session stops as described above. Separate writes before `session.run()` are
not automatically enrolled.

The executable
[atomic adapter example](https://github.com/mathisarends/harnessed/blob/main/examples/applications/_atomic_commits.py)
maps `InboxAddedEvent` to an accepted user message and `AssistantMessageEvent` to
an assistant message. `UserMessageEvent` also records an admitted input during
execution, so treating both input events as separate domain messages would
create duplicates. Applications choose their own projections, metadata and
final/draft-message semantics; an assistant event can be an intermediate tool
reply. Stable message IDs support application upserts. The
[repository](https://github.com/mathisarends/harnessed/blob/main/examples/applications/_atomic_repository.py)
uses a real SQLite transaction for this offline example, with no SDK imports.
Run `uv run python -m examples.applications.18_atomic_commits`. No database driver,
Unit-of-Work abstraction or web-framework integration is added to Harnessed.

Handlers can ignore `changes` when only recovery state is needed; the checkpoint
is always the complete current state, not an incremental journal.

### Store checkpoints as typed documents

`SessionCheckpoint` is a frozen, Pydantic-validated dataclass:

| Field | Meaning |
| --- | --- |
| `session_id` | The session this checkpoint reopens |
| `state` | Typed terminal `SessionState` before the open turn, or the current idle conversation |
| `offset` | Sequence position where the execution suffix begins |
| `seed` | Typed pending inputs and active approval grants |
| `events` | Discriminated built-in events plus explicit `CheckpointExtension` values |
| `control` | Typed `SessionControlChangedEvent` with its `ControlState` |
| `assets` / `files` | Resources needed in addition to the conversation baseline |
| `version` | Current checkpoint format, `Literal[5]` |

For example, `checkpoint.state.history[i].message` is the SDK's typed message
union. Narrow with `isinstance(message, UserMessage)` from `harnessed.models`.
The baseline may be compacted and excludes an open turn; it is not a complete
application message archive. Your domain messages remain application-owned.

Store the checkpoint as one document and keep the queryable domain data in your
own tables. With PostgreSQL, a `JSONB` column fits the document:

```python
document = checkpoint.to_dict()  # JSON-compatible dict for a JSONB column.
restored = SessionCheckpoint.from_dict(document)

text = checkpoint.to_json()  # Or a canonical JSON string.
restored = SessionCheckpoint.from_json(text)
```

`SessionState` has the same four methods. They perform no I/O and need no plugin
catalog. Missing required fields, unsupported versions and invalid built-in events
raise `SessionFormatError`. Store `checkpoint.version` beside the document if your
schema needs to select rows by format. Pydantic `TypeAdapter(SessionCheckpoint)`
supplies schema generation, and the dataclass can be nested in a technical
`BaseModel`; applications do not need to embed an SDK type in their domain models.
Serialization is synchronous; offload large documents in async handlers.
The [typed document example](https://github.com/mathisarends/harnessed/blob/main/examples/applications/17_typed_documents.py)
keeps its [repository](https://github.com/mathisarends/harnessed/blob/main/examples/applications/_conversation_repository.py)
free of SDK imports.

Checkpoints embed the bytes of retained images and spilled text. Keep large
attachments in application storage when the document size matters.

Custom durable events become `CheckpointExtension(tag=..., data=...)` values.
Their recursively immutable JSON payload is the explicit application-defined
boundary. Documents preserve extensions without registering or importing plugins.
When reopening, Harnessed validates each extension against the agent's explicitly
registered event class before starting execution. Unknown tags and invalid
application payloads fail restoration. Built-in events retain their concrete
Python types throughout storage and restoration.

## Persist a deliberate pause

```python
from harnessed.models import UserMessage

async with agent.session(id="investigation", on_commit=commits) as session:
    handle = await session.submit("Investigate the outage.", max_steps=20)
    await session.pause()
    await session.append_message(UserMessage("Checks passed."))

# The previous Python handles close; the stored execution remains paused.
state = await commits.load("investigation")
async with agent.session(state=state, on_commit=commits) as session:
    await session.resume()
    await session.wait_until_idle()
    print(session.messages[-1].text)
```

Closing a paused session that has a commit handler delivers its open turn,
completed steps, limits, queued inputs and active approval grants. Reopening keeps
it paused until `resume()`; committed tools are not replayed. Live result handles,
Python stacks, plugin counters, processes and connections do not survive closure.
Closing an unpaused session still aborts and cleans up its unfinished work.

After an unexpected interruption, reopening repairs incomplete model calls and
tool batches before starting work. An active interrupted turn is ended as aborted.
An interrupted tool's external outcome may be unknown; it is not automatically
executed again. Pending approval waiters are interrupted rather than resurrected.
Register the same custom event classes and explicitly supply validators for
pending structured work with
`agent.session(state=checkpoint, restore_outputs=[YourOutputTypeOrContract])`.
Restoration matches validator identity and schema; stored JSON never imports
Python code.

Execution checkpoints have format **5** and contain their session ID, a
conversation snapshot plus the current execution suffix, queue, grants and
resources. Formats are not automatically migrated. To create an independent
branch, open the checkpoint's terminal `checkpoint.state` under a new ID.

## Continue from application-owned state

```python
from harnessed import Agent
from harnessed.sessions import SessionState

agent = Agent(model=model, plugins=plugins)

# repository and conversation_id belong to your application.
payload = await repository.load(conversation_id)
state = SessionState.from_json(payload) if payload is not None else None

result = await agent.run("Continue investigating the issue.", state=state)
await repository.save(conversation_id, result.state.to_json())
print(result.text)
```

Omit `state` to start empty. Supply a `SessionState` to continue its conversation.
Each open session without `id=` has a fresh runtime ID for correlation. Your application owns
conversation keys, authorization, revisions, deletion and expiration. Coordinate
concurrent writes through your repository, for example with revision checks.

For several turns in one live session:

```python
async with agent.session(state=state) as session:
    await session.run("Inspect the project.")
    result = await session.run("Explain your findings.")
    next_state = session.snapshot()

await repository.save(conversation_id, next_state.to_json())
```

`result.state` belongs to that result's terminal turn boundary. Awaiting an old
handle after a later turn finishes still returns the old turn's snapshot.
`session.snapshot()` captures the current idle conversation and rejects an open
turn, paused session, queued input or closing/closed session. A snapshot can be
taken immediately after an awaited single run. Snapshot creation does not call
a model, trigger compaction or write to a storage backend.

Terminal stop, abort, rejection and limit outcomes can also produce state after
local cleanup closes outstanding tool calls. Exporting a paused execution or
pending approval is not supported. A discarded queued run has no executed turn;
its result carries the most recent terminal conversation state when another turn
is active. Queued work is never imported by a conversation snapshot.

## Snapshot contents and compaction

Snapshots contain the active summary, retained messages and the small amount of
metadata needed for plugin session values, observation provenance and future compaction.
Full typed assistant messages preserve provider replay data and tool-call/result
pairs. The final assistant reply is included; exporting only the last model
request would omit that reply.

Compaction replaces the working context with its new summary and selected
messages. Subsequent summaries replace earlier ones. The discarded raw history,
old requests and old execution events do not enter the snapshot. Runtime owners
also release discarded context; current-turn result buffering may retain it until
that turn ends. Caller-held results and earlier snapshots remain independent
owners of their data. Compaction cannot revive a turn it previously discarded.

The configured model, system instructions, tool definitions and plugin instances
are provided by the new `Agent`. Static system instructions do not accumulate
when importing state. Provider replay data retains its original identity and an
incompatible model binding fails explicitly. Generation and output settings are
provided for each new turn. Python validators and pending output contracts are
not imported. Approval grants and plugin counters belong to the live session;
new sessions evaluate their current approval policy again.

`SessionState.to_json()` and `SessionState.from_json()` use state format **4**.
Unknown versions, unknown fields, incomplete tool pairs and corrupt stored
resources fail explicitly. Old JSONL event logs are not conversation snapshots
and are not automatically migrated. The standalone audit-event codec has its own
versioned envelope.

## Independent continuations

```python
a = await agent.run("Explore approach A.", state=next_state)
b = await agent.run("Explore approach B.", state=next_state)
```

The input snapshot is unchanged. Each continuation owns separate mutable runtime
containers and returns its own state. No fork object, parent archive or implicit
merge is involved.

## Image assets and memory

Inline images are normalized to `ImageReference(AssetRef(...))` values. Use
`await session.assets.put(data, media_type="image/png")` when screenshot bytes
are already available, and `await session.assets.read(ref)` to read verified
bytes while the session is open. Import `ImageReference` from `harnessed.models`.
Only selected images are resolved for model transport after compaction and budget
checks. The encoded-image cache is bounded to 16 MiB and 256 entries.

A snapshot includes the bytes of every image referenced by its retained context.
JSON encodes those bytes as Base64; there are no additional asset directories.
Size and SHA-256 are validated on import. Closing the originating session or
compacting it later cannot invalidate an earlier snapshot. HTTP(S) image URLs
remain external references; Harnessed does not download them automatically.
Explicit `File(path)` and `Image(path)` attachments still read caller-selected
files. Explicit filesystem or shell tools retain their own I/O behavior.

The optional `Spill` plugin uses session-local memory for large textual outputs.
Snapshots include retained spill references and contents. Summaries conservatively
retain active spill dependencies; their explicit count/byte quotas still apply.
Expired references remain unavailable. Session-owned text and image resources are
released when their live owner closes; exported snapshots own their bytes.
These limits are not a total process memory limit: active provider requests,
caller-held snapshots, results and un-compacted context also consume memory.

## Submit, steer and observe

```python
first = await session.submit("Inspect the project.")
second = await session.submit("Then write a summary.")
print((await first.result()).text)
print((await second.result()).text)
```

`submit()` acknowledges admission to the in-memory inbox and returns a
`RunHandle[T]`: a reference to one queued, executing or completed run, rather
than to the whole session. It exposes `id`, `done()`, `result()` and `cancel()`;
it has no event iteration or `aclose()`. Use `run()` when you only need to await
the result immediately. Runs execute in queue order. Cancelling a result waiter
does not cancel the queued or executing run. Repeated waits return the same
result and validated output. Closing the session fails unfinished live handles
with `SessionClosed`.

When a turn fails with an exception, such as a model provider error or a raising
plugin observer, its handle raises that error. The failure also discards every
queued run: each is recorded as `InboxRemovedEvent(reason=DISCARDED)`, its handle
raises the same error, and none of them executes later. Submit them again once
the cause is fixed. A failed commit is the exception: the session rejects further
appends and keeps its queue in the last stored checkpoint, see
[when a commit fails](#when-a-commit-fails).

`await handle.cancel()` explicitly targets this run. A queued run is discarded;
an active turn is aborted and its cleanup is drained. Other queued runs remain
and continue automatically after an active cancellation. Repeated cancellation
of a completed run is harmless. Cancelling the cancellation waiter does not
withdraw its accepted operation. `session.stream()` opens a `RunStream[T]`,
which extends `RunHandle[T]` with live event iteration and observation cleanup
inside its context; see [streaming](streaming.md).

`steer(text)` adds input to the next step of an active turn, or starts a new turn
while idle. `append_message(UserMessage(content))` appends quiet background for subsequent model
requests without starting work. `observe(observation)` buffers the latest pending snapshot per source until
the next step records it. Unrecorded observations are not exported. Use these
operations from hooks as described in [context](context.md).

## Control a live run

```python
from harnessed.models import UserMessage

handle = await session.submit("Investigate the outage.")
await session.pause()
await session.append_message(UserMessage("Checks passed."))
await session.resume()
result = await handle.result()
await session.wait_until_idle()
```

`pause()` waits for a whole model/tool step. `resume()` continues the same open
session. `stop()` completes the current step and ends the turn;
`abort()` cancels activity, drains cleanup and closes outstanding calls. Both
return the live session to idle. Queued runs are discarded unless
`keep_inbox=True`; retained inputs start separate turns. A failing turn discards
queued runs the same way. `wait_until_idle()` also
waits through a pause. Driver hooks cannot wait for their own control boundary.

Concurrent `aclose()` calls share cleanup. Cancelling a close waiter does not
cancel cleanup for other callers. Live processes and connections are released
once. Persisting a conversation does not preserve external workspace state,
background shell commands or Python execution stacks.

## Step progress and a reserved summary

`PreStep` and `PrepareRequest` carry a `TurnProgress` from `harnessed.hooks` as
`event.progress`. It holds the `turn_id`, the current `step`, the effective
`max_steps` and the turn's `final_step` policy. `remaining_steps` includes the
current step, `is_last_step` marks the last step, `is_final_step` marks the
reserved summary step without tools, and `last_tool_step` is the last step that
still offers tools.

```python
from harnessed import Plugin, hook
from harnessed.hooks import Next, PrepareRequest
from harnessed.models import ModelRequest, UserMessage


class WrapUp(Plugin):
    @hook(PrepareRequest)
    async def _remind(self, event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
        request = await next_()
        if event.progress.step == event.progress.last_tool_step:
            request = request.append_message(UserMessage("This is your last step with tools."))
        return request
```

Use `run(..., max_steps=8, token_budget=50_000)` to bound a turn. Token budgets
count input and output across that turn's model calls. `AgentLoop` can reserve a
final summary with `FinalStepPolicy.SUMMARIZE`; this produces text and
`StopReason.MAX_STEPS`, not a validated structured output. See [step limits](../reference/sdk.md).

## Application-owned audit

Use `session.subscribe()` or event hooks to write a complete audit externally.
Subscriptions receive execution events in admission order; `transient=True` also
includes live notifications. Start the consumer before work begins. Consumers
with a full buffer are disconnected with `StreamOverflow`; they do not stop the
producer. Already returned `RunResult.events` remain available to their caller.

`session.events`, `session.turns` and request lookup describe the current turn's
execution buffer, which is released after terminal delivery. They are not an
archive of earlier runs. `session.messages` exposes retained working context;
`session.last(Event)` provides limited recent diagnostics and can be cleared by
compaction. Use snapshots for continuation and an external audit for historical
inspection. [Event extensions](events.md) remain available for typed application
records; custom audit events do not automatically become conversation state.
