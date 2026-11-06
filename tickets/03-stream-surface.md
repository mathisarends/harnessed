# 03 — Stream surface for UIs

- **Status:** proposed
- **Priority:** high
- **Breaking:** no (additive)
- **Depends on:** 01

## Problem

1. `ToolExecutionStartedEvent` is durable but not part of `StreamEvent[T]`. A
   UI that consumes `session.stream()` sees `ToolCallRequestedEvent`, sometimes
   `ApprovalNeededEvent`, and then `ToolCallFinishedEvent`. It cannot tell
   "waiting for approval" apart from "running", nor show a spinner when
   execution actually begins.
2. `harnessed.events` exports about 80 names in one flat namespace. A UI author
   cannot tell from an import which events can arrive on `session.stream()` and
   which exist only in the log (`session.events`). `docs/reference/events.md`
   has a *Durable* column, but the code doesn't show the split.
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
        case ToolExecutionStartedEvent(call_id=call_id):     # new on the stream
            ui.tool_running(call_id)
        case ToolProgressEvent(call_id=call_id, text=text):
            ui.tool_progress(call_id, text)
        case ToolCallFinishedEvent(call=call, result=result):
            ui.tool_done(call.id, result)
```

### Optional: role-scoped import paths

The flat root stays and remains canonical. Two read-only submodules re-export
the same class objects grouped by where they appear:

```python
from harnessed.events.stream import TextDeltaEvent, ToolExecutionStartedEvent  # what stream() yields
from harnessed.events.log import TurnEndedEvent, ModelUsageRecordedEvent       # what session.events holds
```

- `harnessed.events.stream.__all__` == the members of `StreamEvent[Any]`.
- `harnessed.events.log.__all__` == the members of `LogEvent`.
- An event present in both is exported from both, as the same object.
- A test asserts both `__all__` lists against the unions, so they cannot drift.

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
- New: `harnessed/events/stream.py`, `harnessed/events/log.py` (if accepted).
- Tests: stream ordering test for requested → approval → execution started →
  finished, plus the `__all__` drift tests.
- Docs: `docs/guides/events.md` (UI example like the one above),
  `docs/reference/events.md`.

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

1. Add the `stream` / `log` submodules, or only document the split? The ticket
   recommends adding them. They cost little, need no aliases (same objects),
   and make autocomplete useful.
2. Rename `EventStream[T]` → `StreamItem[T]` (breaking type alias rename), or
   keep it? The ticket recommends renaming before the first stable release.
