# Conversations, persistence and explicit state

`Agent` holds reusable configuration. `Agent.session()` opens a live conversation;
`SessionState` is an immutable value that your application can save and inject
into another session. Sessions are in memory by default. Supplying an explicit
`SessionStore` enables automatic checkpoints and reopening by application-owned ID.

## Opt into JSON persistence

```python
from pathlib import Path

from harnessed import Agent
from harnessed.integrations.json import JsonSessionStore

agent = Agent(
    model=model,
    instructions="Include source URLs.",
    store=JsonSessionStore(root=Path(".harnessed/sessions")),
)

async with agent.session(id="voyager") as session:
    result = await session.run("Compare the Voyager missions.", max_steps=20)

# A later request or process opens the same conversation.
async with agent.session(id="voyager") as session:
    result = await session.run("Explain their different trajectories.")
```

The JSON implementation ships in the base package and requires no extra. It
creates its directory on the first save. Without `store=`, no session directory
is created and an explicit `id=` raises `ConfigError`. With a store, omitting
`id` generates a fresh ID; retain `session.id` if it must be reopened.

The session owns one background writer. Intermediate changes mark its latest state
dirty and are coalesced into a checkpoint requested after 100 ms; no separate queue
of checkpoint copies grows with the event rate. Checkpoint JSON encoding and file
I/O run in workers. Model and tool activity can overlap a background save; state
admission and writes remain serialized within each session.

Durability barriers await committed state: input admission/removal, model request
intent, tool execution intent and receipts, reviewed tool results, turn completion,
controls, approval changes, feature-owned instruction state, compaction and custom durable events.
Thus an awaited `run()` has saved its final reply, and `pause()` waits for the
completed step's checkpoint. Transient token notifications and unrecorded
observations are not saved. Intermediate internal events may be lost in a crash
before the next checkpoint, while external-action intent and acknowledged results
retain their commit guarantees.

The live view can advance beyond the persisted view while a model is streaming.
Text/reasoning deltas and tool-argument previews use `TransientEvent`: they never
enter the checkpoint or `SessionCommit.changes` and do not schedule writes. The
assembled assistant message, including a captured interrupted partial reply, is
recorded as one durable event. Other request/observation metadata can still be
committed during a stream; those writes are independent of token frequency.

`ToolProgressEvent` is currently a durable intermediate event, so continuous tool
progress is batched after roughly 100 ms or at the next mandatory barrier. For
frequent UI-only progress, declare a custom `TransientEvent`. Custom `DurableEvent`
values are mandatory barriers and await a commit for each emitted event. Named
context changes are also durable, rather than a destination for live draft tokens.

Use `await session.flush()` to commit pending state explicitly. Closing drains the
writer before releasing its resources. Without a store, `flush()` performs no I/O.
The writer reuses image and text bytes already present in its conversation baseline
instead of embedding a second copy in the same checkpoint.

A failed background save stops further admissions, cancels active work for cleanup
and reaches `run()`, `RunHandle.result()` or the stream iterator. A previously
unobserved idle failure is also reported when closing. Applications map these
exceptions to their HTTP, SSE, WebSocket or job-status API; ignoring a submitted
handle does not create a frontend notification channel. Reopen after resolving
the storage failure.

JSON stores use hashed filenames, a revision envelope, a flushed temporary file
and atomic replacement. An OS writer lock protects revision checking and writing
across local processes. Lock files remain in place. Choose a local filesystem
supporting locking and atomic replacement; this is not a distributed lease or a
network-filesystem coordination service. Each update serializes the current
checkpoint, including resource bytes, rather than appending an audit log.

## Use an existing conversation repository

Import `SessionCommit`, `SessionCheckpoint`, `SessionStore` and `StoredSession` from
`harnessed.sessions`. A store implements two async methods structurally:

```python
from harnessed.sessions import SessionCheckpoint, SessionCommit, SessionStore, StoredSession


class ConversationSessionStore:
    async def load_session(self, conversation_id: str) -> StoredSession | None: ...

    async def save_session(
        self,
        conversation_id: str,
        commit: SessionCommit,
        *,
        expected_revision: int | None,
    ) -> int: ...


store: SessionStore = ConversationSessionStore(...)
agent = Agent(model=model, store=store)
```

`StoredSession(checkpoint, revision)` contains a typed checkpoint and a positive
revision. Harnessed passes an immutable exchange value to the adapter; it does
not prescribe your domain models, database columns or repository method names.
Keep an existing repository independent of Harnessed and map its own records
inside this application-owned adapter. Implementing the protocol directly is
also possible. The agent never closes an application-owned store or pool.

The executable
[adapter example](https://github.com/mathisarends/harnessed/blob/main/examples/39_typed_session_store.py)
wraps a [repository with no SDK imports](https://github.com/mathisarends/harnessed/blob/main/examples/_conversation_repository.py).
It maps typed checkpoint information to application-owned fields and translates
the repository's conflict error to `SessionWriteConflict`. Its storage layout is
illustrative; applications can map fields into their own records instead of
storing one document.

### Commit application changes atomically

Each `save_session` receives one frozen, Pydantic-validated `SessionCommit`:

```python
checkpoint = commit.checkpoint
for change in commit.changes:
    position = change.sequence  # Conversation-local, zero-based event position.
    event = change.event  # Concrete built-in event or CheckpointExtension.
```

`changes` contains all new durable events since the previous acknowledged save,
in order. Sequences are contiguous and end at `commit.next_sequence`, which is
`checkpoint.offset + len(checkpoint.events)`. The runtime retains these immutable
event references until the store acknowledges the commit, including events that
compaction removes from working history. A failed save poisons the binding; the
SDK does not silently discard changes or automatically retry uncertain commits.
Reopening restores the checkpoint without replaying old changes into the store;
recovery repairs can generate new changes. Token deltas are transient and are
excluded. Importing a terminal `SessionState` does not synthesize historical
changes. Manually constructed `SessionCommit(checkpoint)` values can seed storage
without an event archive; runtime saves always include their pending changes.

To keep domain messages and recovery state atomic, map the changes inside your
adapter and apply them with the checkpoint in one application-owned transaction:

```text
BEGIN
  Apply mapped message/domain changes
  Compare the revision and write the checkpoint
COMMIT
```

Raise on a revision conflict so the transaction rolls back both writes. Return
only after commit; storage errors then reach the waiting run/result/stream caller.
Model and tool execution happens between short transactions, outside DB locks.
The SDK passes a commit request; your repository implements its atomicity.
Separate writes before `session.run()` are not automatically enrolled.

The executable
[atomic adapter example](https://github.com/mathisarends/harnessed/blob/main/examples/_atomic_session_store.py)
maps `InboxAddedEvent` to an accepted user message and `AssistantMessageEvent` to
an assistant message. `UserMessageEvent` also records an admitted input during
execution, so treating both input events as separate domain messages would
create duplicates. Applications choose their own projections, metadata and
final/draft-message semantics; an assistant event can be an intermediate tool
reply. Stable message IDs support application upserts. The
[repository](https://github.com/mathisarends/harnessed/blob/main/examples/_atomic_repository.py)
uses a real SQLite transaction for this offline example, with no SDK imports.
Run `uv run python -m examples.40_atomic_session_store`. No database driver,
Unit-of-Work abstraction or web-framework integration is added to Harnessed.

Adapters can ignore `changes` when only recovery state is needed. The bundled
JSON store does this: its file remains a current checkpoint, not a domain-message
archive or an incremental journal. Checkpoint and JSON-envelope formats are 3.
Application-specific event payloads use the same `CheckpointExtension` boundary
as checkpoints. Resource references are SDK event data; retained checkpoint
resources cover recovery, while long-term application attachment storage remains
application-owned.

A lost commit acknowledgement can leave the client uncertain even when the DB
committed. Reopen and reconcile using revisions/application IDs rather than
blindly rerunning external actions. Transient streaming text may arrive before
its commit; successful terminal completion still awaits committed storage.

### Inspect and encode typed checkpoints

`SessionCheckpoint` is a frozen, Pydantic-validated dataclass:

| Field | Meaning |
| --- | --- |
| `state` | Typed terminal `SessionState` before the open turn, or the current idle conversation |
| `offset` | Sequence position where the execution suffix begins |
| `seed` | Typed pending inputs and active approval grants |
| `events` | Discriminated built-in events plus explicit `CheckpointExtension` values |
| `control` | Typed `SessionControlChangedEvent` with its `ControlState` |
| `assets` / `files` | Resources needed in addition to the conversation baseline |
| `version` | Current checkpoint format, `Literal[3]` |

For example, `checkpoint.state.history[i].message` is the SDK's typed message
union. Narrow with `isinstance(message, UserMessage)` from `harnessed.models`.
The baseline may be compacted and excludes an open turn; it is not a complete
application message archive. Your domain messages remain application-owned.

Construct checkpoints from your repository's typed records, or use the optional
canonical JSON codec at the persistence boundary:

```python
document = checkpoint.to_json()
restored = SessionCheckpoint.from_json(document)
```

The codec performs no I/O and needs no plugin catalog. Missing required fields,
unsupported versions and invalid built-in events fail validation. Pydantic
`TypeAdapter(SessionCheckpoint)` supplies schema generation and serialization.
The dataclass can also be nested in a technical `BaseModel` with
`model_dump_json()` / `model_validate_json()`; applications do not need to adopt
Pydantic or embed an SDK type in their domain models. Serialization is synchronous;
offload large documents in async adapters. The bundled JSON store performs its
encoding and file I/O in a worker. Typed in-memory stores need no JSON conversion.

Custom durable events become `CheckpointExtension(tag=..., data=...)` values.
Their recursively immutable JSON payload is the explicit application-defined
boundary. Stores preserve extensions without registering or importing plugins.
When reopening, Harnessed validates each extension against the agent's explicitly
registered event class before starting execution. Unknown tags and invalid
application payloads fail restoration. Built-in events retain their concrete
Python types throughout storage and restoration.

Saving must atomically compare the stored revision with `expected_revision`.
None requires a missing entry and returns revision 1; an update returns the
previous revision plus one. Raise `SessionWriteConflict` from `harnessed.errors`
when the expected revision does not match. Return only after commit. Keep database
transactions local to each store call, outside model and tool execution.
Authorization, retention, connection pools and the backend schema remain owned
by the application. To include domain writes, perform them in the same repository
transaction as the checkpoint, using `commit.changes` as the incremental input.

Revisions prevent lost updates; they do not stop two workers from executing tools
before one loses the write race. Applications must serialize execution for each
conversation through their job runner or distributed ownership mechanism. Do not
automatically retry a conflicted run: its external actions may already have happened.

## Persist a deliberate pause

```python
from harnessed.models import UserMessage

async with agent.session(id="investigation") as session:
    handle = await session.submit("Investigate the outage.", max_steps=20)
    await session.pause()
    await session.append_message(UserMessage("Checks passed."))

# The previous Python handles close; the persisted execution remains paused.
async with agent.session(id="investigation") as session:
    await session.resume()
    await session.wait_until_idle()
    print(session.messages[-1].text)
```

Closing a persistent paused session preserves its open turn, completed steps,
limits, queued inputs and active approval grants. Reopening keeps it paused until
`resume()`; committed tools are not replayed. Live result handles, Python stacks,
plugin counters, processes and connections do not survive closure. Closing an
unpaused session still aborts and cleans up its unfinished work.

After an unexpected interruption, reopening repairs incomplete model calls and
tool batches before starting work. An active interrupted turn is ended as aborted.
An interrupted tool's external outcome may be unknown; it is not automatically
executed again. Pending approval waiters are interrupted rather than resurrected.
Register the same custom event classes and explicitly supply validators for
pending structured work with
`agent.session(id="...", restore_outputs=[YourOutputTypeOrContract])`. Restoration
matches validator identity and schema; stored JSON never imports Python code.

Execution checkpoints have format **2** and contain a conversation snapshot plus
the current execution suffix, queue, grants and resources. They are distinct from
`SessionState` JSON and old JSONL archives; formats are not automatically migrated.
Supplying both `state=` and an existing stored ID fails instead of overwriting it.
To create an independent branch, supply a terminal `SessionState` and a new ID.

## Continue from application-owned state

```python
from harnessed import Agent, SessionState

agent = Agent(model=model, plugins=plugins)

# repository and conversation_id belong to your application.
payload = await repository.load(conversation_id)
state = SessionState.from_json(payload) if payload is not None else None

result = await agent.run("Continue investigating the issue.", state=state)
await repository.save(conversation_id, result.state.to_json())
print(result.text)
```

Omit `state` to start empty. Supply a `SessionState` to continue its conversation.
Without a store, each open session has a fresh runtime ID for correlation. Your application owns
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
metadata needed for feature-owned instructions, observation provenance and future compaction.
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

`SessionState.to_json()` and `SessionState.from_json()` use state format **1**.
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
`RunHandle[T]`. Runs execute in queue order. Cancelling a result waiter does not
cancel the queued or executing run. Repeated waits return the same result and
validated output. Closing fails unfinished live handles with `SessionClosed`.

`await handle.cancel()` explicitly targets this run. A queued run is discarded;
an active turn is aborted and its cleanup is drained. Other queued runs remain
and continue automatically after an active cancellation. Repeated cancellation
of a completed run is harmless. Cancelling the cancellation waiter does not
withdraw its accepted operation. A handle opened with `session.stream()` also
iterates live events inside its context; see [streaming](streaming.md).

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
`keep_inbox=True`; retained inputs start separate turns. `wait_until_idle()` also
waits through a pause. Driver hooks cannot wait for their own control boundary.

Concurrent `aclose()` calls share cleanup. Cancelling a close waiter does not
cancel cleanup for other callers. Live processes and connections are released
once. Persisting a conversation does not preserve external workspace state,
background shell commands or Python execution stacks.

## Step progress and a reserved summary

`PreStep` exposes `max_steps`, `remaining_steps`, `is_last_step` and `is_final_step`.
The remaining count includes the step about to start.

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
