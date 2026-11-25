# Conversations, persistence and explicit state

`Agent` holds reusable configuration. `Agent.session()` opens a live conversation;
`SessionState` is an immutable value that your application can save and inject
into another session. Harnessed does not store anything itself. Sessions are in
memory, and the application decides where their state goes: it receives each
durable change through an `on_commit` handler and reopens a conversation by passing
the stored value back as `state=`.

A persisted session has two parts. The **boundary** (`SessionBoundary`) is the
session at its last rest point: the closed conversation, its controls and queued
inputs. The **journal** is the append-only sequence of `JournalEntry` values
recorded since then: inputs, model requests, tool calls and their results, one
entry per durable event. A store writes each entry once and replaces the
boundary only when a new one arrives. Reopening loads the boundary and replays
the journal, the same way a database recovers from its last checkpoint and its
write-ahead log.

## Receive commits

```python
from harnessed import Agent
from harnessed.sessions import SessionCheckpoint, SessionCommit


class ConversationCommits:
    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def __call__(self, commit: SessionCommit) -> None:
        previous = await self.load(commit.session_id)
        checkpoint = SessionCheckpoint.from_commit(commit, previous)
        await self._repository.save(commit.session_id, checkpoint.to_dict())

    async def load(self, conversation_id: str) -> SessionCheckpoint | None:
        document = await self._repository.load(conversation_id)
        return SessionCheckpoint.from_dict(document) if document is not None else None


commits = ConversationCommits(repository)
agent = Agent(model=model, instructions="Include source URLs.")

async with agent.session(id="voyager", on_commit=commits) as session:
    await session.run("Compare the Voyager missions.", max_steps=20)

# A later request or process continues the same conversation.
state = await commits.load("voyager")
async with agent.session(state=state, on_commit=commits) as session:
    await session.run("Explain their different trajectories.")
```

This handler keeps one document per conversation: `SessionCheckpoint.from_commit()`
folds each commit into the previously stored checkpoint. It is the smallest
store, but it rewrites the whole conversation at every commit. Production stores
keep the boundary and the journal apart, as
[described below](#store-the-boundary-and-the-journal).

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
If the caller cancels `submit()` during its input commit and the commit succeeds,
the session runs the accepted input even though the caller receives no handle.
Pass `commit_timeout_seconds=` to `agent.session()` to bound each delivery. When
the deadline expires, the handler is cancelled and the commit fails with
`CommitTimeout` from `harnessed.errors`, following the failed-commit path below.
Subagent sessions share their parent's deadline. Choose a deadline that leaves
room for storing large checkpoints.

Other durable events, such as `ToolProgressEvent`, are collected and travel with
the next barrier, `await session.flush()` or close. Text/reasoning deltas and
tool-argument previews are `TransientEvent` values: they never enter a checkpoint
or a journal entry. The assembled assistant message, including a captured
interrupted partial reply, is recorded as one durable event. Custom `DurableEvent`
values are barriers and await a commit for each emitted event; declare a custom
`TransientEvent` for frequent UI-only progress.

Subagent sessions deliver their commits through their parent's handler, each
under its own `commit.session_id`. Store one row per session. A session's first
commit always carries its boundary, and a child's
`commit.boundary.state.parent.parent_session_id` links it to its parent:

```python
async def save(commit: SessionCommit) -> None:
    if commit.boundary is not None:
        parent = commit.boundary.state.parent
        await repository.save_boundary(
            commit.session_id,
            commit.boundary.to_dict(),
            parent_id=parent.parent_session_id if parent is not None else None,
        )
    await repository.append(commit.session_id, [entry.to_dict() for entry in commit.journal])
```

### Store the boundary and the journal

Each `SessionCommit` carries the journal entries recorded since the previous
delivered commit and, when the session reached a new rest point, its new
boundary:

| Member | Meaning |
| --- | --- |
| `session_id` | The session the commit belongs to |
| `journal` | New `JournalEntry` values, at least one, with contiguous sequences |
| `boundary` | The new `SessionBoundary`, or `None` while it is unchanged |
| `next_sequence` | The sequence the next entry will receive |

A commit carries a boundary when the conversation state differs from the
previous delivered boundary: with the session's first commit, at turn end, after
idle compaction and after idle changes such as `append_message()`. Commits
inside a turn and idle input admission carry only journal entries. Append the
entries and replace the boundary in one transaction:

```python
from harnessed.sessions import JournalEntry, SessionBoundary, SessionCheckpoint, SessionCommit


async def save(commit: SessionCommit) -> None:
    async with repository.transaction() as tx:
        for entry in commit.journal:
            # Keyed by (session, sequence); a redelivered entry is ignored.
            await tx.append_entry(commit.session_id, entry.sequence, entry.to_dict())
        if commit.boundary is not None:
            await tx.save_boundary(commit.session_id, commit.boundary.to_dict())


async def load(session_id: str) -> SessionCheckpoint:
    boundary, entries = await repository.load(session_id)
    return SessionCheckpoint.from_journal(
        SessionBoundary.from_dict(boundary),
        [JournalEntry.from_dict(entry) for entry in entries],
    )
```

`SessionCheckpoint.from_journal()` skips entries recorded before the boundary,
so a store may keep old entries as an audit trail or delete them once a newer
boundary is stored. It rejects a journal with a gap after the boundary.
`Agent.session(state=...)` validates the result like any checkpoint.

A boundary's `sequence` is the position of the first entry that follows it. Its
`state` holds the history, retained images and spill text; `seed` holds the
queued inputs, approval grants and pending approval requests; `control` holds
the session controls. While a turn runs, the boundary stays the one taken when
the turn started, so a store writes it at most once per turn. Each entry's
`record` is a built-in durable event, a `CheckpointExtension` for an application
event, or a `CheckpointRequest` for a model request. An entry carries the images
and spill text first stored with it, so every resource is written once, either
with a boundary or with one entry.

A model request lists every message it sends, so the journal stores it as a
`CheckpointRequest`: a message equal to one in the turn's boundary state, or to
one in an earlier request of the same turn, is stored as its ID. A message that
a `PrepareRequest` hook or compaction changed is stored in full, so replay
rebuilds exactly the request the model received. `checkpoint.events` always
holds the complete `ModelRequestedEvent`. A store therefore writes an amount of
data per turn that depends on that turn, not on the conversation before it. See
[the runnable journal example](https://github.com/mathisarends/harnessed/blob/main/examples/applications/23_journal_commits.py)
and the
[JSONL file example](https://github.com/mathisarends/harnessed/blob/main/examples/applications/16_session_commits.py).

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
for entry in commit.journal:
    position = entry.sequence  # Conversation-local, zero-based event position.
    record = entry.record  # Built-in event, CheckpointExtension or CheckpointRequest.
```

`journal` contains all new durable events since the previous delivered commit, in
order. Sequences are contiguous and end before `commit.next_sequence`. They
include events that compaction removes from working history. Reopening does not
deliver old entries again; recovery repairs can record new ones. Importing a
terminal `SessionState` does not synthesize historical entries.

To keep domain messages and recovery state consistent, map the entries inside
your handler and write them with the journal and boundary in one
application-owned transaction:

```text
BEGIN
  Insert or upsert mapped message/domain rows
  Insert the journal entries
  Upsert the boundary, if the commit carries one
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

The domain rows are a projection of the journal; the journal and the boundary
remain the recovery state.

### Store checkpoints as typed documents

`SessionCheckpoint` is a frozen, Pydantic-validated dataclass that holds a
boundary and the journal that continues it:

| Member | Meaning |
| --- | --- |
| `boundary` | The `SessionBoundary` the session reopens from |
| `journal` | The `JournalEntry` values after the boundary, with at most one open turn |
| `session_id` | The boundary's session ID |
| `state` | The boundary's terminal `SessionState` |
| `events` | The journal's records as events, with model requests resolved |
| `assets` / `files` | Resources held by the boundary and the journal |
| `version` | Current checkpoint format, `Literal[6]` |

For example, `checkpoint.state.history[i].message` is the SDK's typed message
union. Narrow with `isinstance(message, UserMessage)` from `harnessed.models`.
The baseline may be compacted and excludes an open turn; it is not a complete
application message archive. Your domain messages remain application-owned.

A checkpoint can be stored as one document while the queryable domain data stays
in your own tables. With PostgreSQL, a `JSONB` column fits the document:

```python
document = checkpoint.to_dict()  # JSON-compatible dict for a JSONB column.
restored = SessionCheckpoint.from_dict(document)

text = checkpoint.to_json()  # Or a canonical JSON string.
restored = SessionCheckpoint.from_json(text)
```

`SessionState`, `SessionBoundary` and `JournalEntry` have the same four methods. They perform no I/O and need no plugin
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

Checkpoints have format **6**; boundaries and journal entries have format **1**.
Formats are not automatically migrated. To create an independent
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

When assigning IDs to user messages, reserve `system` and the `mount-context:`
prefix for agent configuration. Submitting, steering, appending or observing a
message with one of those IDs raises `ValueError` before admission.

## Read results and usage

```python
async with agent.session() as session:
    result = await session.run("Inspect the project.")

    print(result.usage.total_tokens)  # this turn
    print(session.usage.total_tokens)  # this session, without children
    print(session.tree_usage.total_tokens)  # with recorded child sessions
    print(session.model_request_count)  # model calls, including failed ones

    pages = list(session.plugin(Navigation).pages)
```

The object passed to `plugins=[...]` is a recipe. Each session builds its own
activation from it, so data a plugin collects lives on the activation and never
appears on the recipe. `session.plugin(Navigation)` returns that activation with
its type. When a session mounts the same class twice, pass the recipe to select
one: `session.plugin(primary)`. It raises `LookupError` unless exactly one
activation matches, for example while the plugin waits for a dependency.

Copy what you need before leaving the `async with` block. `PluginState` is
released when the session closes, and `session.plugin()` then raises
`SessionClosed`. A restart creates a new activation with empty state, so call
`session.plugin()` when you need the value instead of keeping the reference
across turns. The usage properties stay readable after closing.

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
