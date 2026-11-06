# 01 — Event package structure

- **Status:** proposed
- **Priority:** high
- **Breaking:** no (public imports from `harnessed.events` stay the same)
- **Depends on:** –

## Problem

Event code is split along historical lines instead of by domain or role:

| File | Lines | Contents |
|------|-------|----------|
| `harnessed/core/session/events.py` | 910 | All durable events across ~12 domains, ~10 enums (`InboxMode`, `TurnOutcome`, `StepOutcome`, `RuleAction`, `ApprovalStatus`, `PolicySource`, …), `LogEvent`, `SESSION_EVENT_TYPES` |
| `harnessed/core/stream.py` | 266 | Transient UI events and the `StreamEvent[T]` / `EventStream[T]` unions |
| `harnessed/core/runtime/streaming.py` | 199 | Log → stream projection (`project_stream_events`) **and** `StreamApprovals` |
| `harnessed/core/event_kinds.py` | – | One `EventKind` enum for durable and transient tags |
| `harnessed/core/event_registry.py` | – | `EventRegistry` for custom events |

Concrete problems:

1. `core/stream.py` imports `harnessed.core.runtime.result.RunResult`, so the
   event layer depends on the runtime layer. `TurnFinishedEvent[T]` carries a
   `RunResult[T]`, which is the only reason for that dependency.
2. Contributors cannot find events by topic. Tool events live in three files,
   approval events in two.
3. Domain enums (`TurnOutcome`, `ApprovalStatus`, …) are defined next to the
   union instead of next to the events that use them.
4. `runtime/streaming.py` mixes a pure projection function with a stateful
   approval helper.

## Target structure

```text
harnessed/core/events/
    __init__.py        # internal re-exports for core; not a public facade
    base.py            # SessionEvent, DurableEvent, transient base, Emit glue
    kinds.py           # EventKind (moved from core/event_kinds.py)
    registry.py        # EventRegistry (moved from core/event_registry.py)
    turn.py            # TurnStarted/Ended/Finished, StepStarted/Ended, TurnOutcome, StepOutcome
    messages.py        # System/User/Context/Assistant message events, ContextValueChanged
    model.py           # ModelRequested/RequestFailed/Attempt*/RetryScheduled/UsageRecorded, CacheMetrics
    tools.py           # ToolCallRequested/Finished, ToolExecution*, ToolProgress, ToolPolicyApplied, deltas
    approvals.py       # ApprovalRequested/Resolved/Revoked/Needed, ApprovalStatus, PolicySource
    inbox.py           # InboxAdded/Removed, InboxMode
    observations.py    # Observation*, Spill*
    compaction.py      # Compaction*
    output.py          # OutputValidated/ValidationFailed/Unavailable
    subagents.py       # Subagent*
    session.py         # SessionStatusChanged, AgentControlChanged
    unions.py          # LogEvent, StreamEvent[T], EventStream[T], SESSION_EVENT_TYPES
harnessed/core/result.py          # RunResult (moved from core/runtime/result.py)
harnessed/core/runtime/streaming.py   # project_stream_events only
harnessed/core/runtime/stream_approvals.py  # StreamApprovals
```

Rules:

- Each domain module owns its events **and** the enums those events use.
- `unions.py` is the only module that imports every domain module.
- No module in `core/events/` imports `core/runtime`.
- `harnessed/events/__init__.py` keeps its exact public names and switches its
  imports to `harnessed.core.events`. `harnessed/events/__init__.py` keeps the
  explicit re-export style that AGENTS.md requires for facades.

Application code does not change:

```python
from harnessed.events import ToolCallFinishedEvent, TurnFinishedEvent  # unchanged
```

## Scope

- Move: `core/session/events.py`, `core/stream.py`, `core/event_kinds.py`,
  `core/event_registry.py`, `core/runtime/result.py`.
- Update all internal imports (`git grep -l "core.session.events\|core.stream\|core.event_kinds\|core.event_registry\|runtime.result"`).
- `smelt.yaml`: point the `events` layer at `harnessed/core/events/**` and
  `harnessed/events/**`. Add a boundary that forbids `core.events → core.runtime`.
- `tests/test_architecture.py`: add the same boundary if smelt cannot express it.
- `docs/contributing/structure-target.md`: describe the new layout.

## Steps

1. Move `RunResult` to `core/result.py`, update imports, and run the checks.
2. Create `core/events/` with `base.py`, `kinds.py` and `registry.py`. Move the code
   without editing it.
3. Split `core/session/events.py` by domain, one module per commit if that
   helps review. Keep `unions.py` as the single place that assembles the unions.
4. Move the stream events into their domain modules (for example,
   `TextDeltaEvent` → `messages.py` and `ToolArgumentsDeltaEvent` → `tools.py`).
5. Split `StreamApprovals` out of `runtime/streaming.py`.
6. Delete the old modules. Per AGENTS.md, no shim modules.

## Acceptance

- Baseline checks from the README pass.
- `harnessed.events.__all__` is unchanged (compare it before and after in a test or
  by hand).
- `uv run smelt context harnessed/core/events` shows no runtime dependency.
- No file in `harnessed/core/events/` exceeds ~250 lines.
- The codec round-trip tests and `tests/core/session/fixtures/*.jsonl` pass
  unchanged, which proves there is no format change.

## Out of scope

- Renaming tags or classes (ticket 02).
- Changing which events are on the stream (ticket 03).
- Removing base classes (ticket 04).

## Decisions

- Keep one `EventKind` enum, or split it into per-domain tag enums? This ticket
  proposes keeping **one** enum in `kinds.py`. One enum is simplest for the
  codec and for `Literal[EventKind.X]` discriminators, and splitting it adds
  churn without user benefit.
