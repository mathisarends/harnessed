# 03 — Stream surface for UIs

- **Status:** proposed
- **Priority:** high
- **Breaking:** only the `EventStream[T]` → `StreamItem[T]` rename (decision 2); the rest is additive
- **Depends on:** 01; problem 2 is solved by ticket 04

## Problem

1. `ToolExecutionStartedEvent` is durable but not part of `StreamEvent[T]`. A
   UI that consumes `session.stream()` sees `ToolCallRequestedEvent`, sometimes
   `ApprovalNeededEvent`, and then `ToolCallFinishedEvent`. It cannot tell
   "waiting for approval" apart from "running", nor show a spinner when
   execution actually begins.
2. `harnessed.events` exports about 80 names in one flat namespace. A UI author
   cannot tell from an import which events can arrive on `session.stream()` and
   which exist only in the log (`session.events`). `docs/reference/events.md`
   has a *Durable* column, but the code doesn't show the split. Ticket 04 fixes
   this with one `DurableEvent` / `TransientEvent` base per event; this ticket
   only documents it.
3. There are several overlapping union aliases: `StreamEvent[T]`,
   `EventStream[T]`, `LogEvent`, `BuiltinEvent`. `EventStream[T]` (what
   `session.stream()` yields) and `StreamEvent[T]` (the built-in subset) look
   alike but mean different things.

## Target API

### Stream includes execution start

```python
async for event in session.stream("Refactor utils.py"):
    match event:
        case ToolCallRequestedEvent(call=call):
            ui.tool_pending(call.id, call.name)
        case ApprovalNeededEvent(call=call, reason=reason):
            ui.tool_awaiting_approval(call.id, reason)
        case ToolExecutionStartedEvent(call_id=call_id):  # new on the stream
            ui.tool_running(call_id)
        case ToolProgressEvent(call_id=call_id, text=text):
            ui.tool_progress(call_id, text)
        case ToolCallFinishedEvent(call=call, result=result):
            ui.tool_done(call.id, result)
```

### Where an event can appear: base class, not import path

There are no `harnessed.events.stream` or `harnessed.events.log` submodules.
The log and the stream are two **consumers** of one event vocabulary, and the
two sets overlap: `ToolCallRequestedEvent` and `ToolCallFinishedEvent` are in
the log and also arrive on the stream. Split import paths would either export
those events twice or make users guess where to import them from. They would
also suggest that `events.log` is the log itself. The log as a mechanism
(`SessionLog`, codec, checkpoints, store) already lives in
`harnessed/core/session/` and does not belong to the event package.

The role is visible on the class instead, through the two bases from ticket 04:

```python
from harnessed.events import DurableEvent, TransientEvent

isinstance(event, DurableEvent)  # in the log (and possibly projected onto the stream)
isinstance(event, TransientEvent)  # stream only, never persisted
```

`docs/reference/events.md` keeps its table and gains an explicit *On stream*
column next to *Durable*, so all four combinations can be read at a glance.

### One name per union

| Today | Target | Meaning |
|-------|--------|---------|
| `EventStream[T]` | `StreamItem[T]` | Everything `session.stream()` can yield, including durable and custom events |
| `StreamEvent[T]` | `StreamEvent[T]` | Built-in transient UI events (unchanged) |
| `LogEvent` | `LogEvent` | Built-in durable events (unchanged) |
| `BuiltinEvent` | removed | `LogEvent \| StreamEvent[Any]`; inline it where it is used |

## Scope

- `core/stream.py` (or `core/events/unions.py` after 01): add
  `ToolExecutionStartedEvent` to `StreamEvent`.
- `core/runtime/streaming.py`: project `ToolExecutionStartedEvent` from the log
  onto the stream.
- `harnessed/events/__init__.py`: `StreamItem`; drop `BuiltinEvent`.
- Tests: stream ordering test for requested → approval → execution started →
  finished.
- Docs: `docs/guides/events.md` (UI example like the one above, plus a short
  "log vs. stream" section), `docs/reference/events.md` (*On stream* column).

## Acceptance

- Baseline checks pass.
- A stream test with an approval-gated tool shows the order
  `ToolCallRequestedEvent`, `ApprovalNeededEvent`, `ToolExecutionStartedEvent`,
  `ToolCallFinishedEvent`.
- A stream test without approval shows `ToolExecutionStartedEvent` before
  any `ToolProgressEvent`.
- Typing contract test in `tests/typing/`: `match` over `StreamItem[str]` is
  exhaustive for `ToolExecutionStartedEvent`.

## Decisions

1. ~~Add `harnessed.events.stream` / `harnessed.events.log` submodules?~~
   Rejected in review: the log and the stream overlap, so role-scoped import
   paths are misleading. The base classes from ticket 04 carry this information.
2. Rename `EventStream[T]` → `StreamItem[T]` (breaking type alias rename), or
   keep it? The ticket recommends renaming before the first stable release.
