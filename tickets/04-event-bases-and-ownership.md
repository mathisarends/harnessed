# 04 — Fewer event bases, plugin-owned events

- **Status:** proposed
- **Priority:** medium
- **Breaking:** yes (public base class names; one event moves to its plugin)
- **Depends on:** 01. Ideally ship with 02.

## Problem

### Too many overlapping base names, and one name means two classes

A custom-event author meets these names:

| Name | Where | What it is |
|------|-------|------------|
| `SessionEvent` | `harnessed.events` | Base of every built-in **durable** event |
| `DurableEvent(SessionEvent)` | `harnessed.events` | Base for **custom** durable events only |
| `core.stream.TransientEvent` | core | Base of every built-in **transient** event |
| `core.stream.CustomTransientEvent(TransientEvent)` | core | Base for custom transient events |
| `harnessed.events.TransientEvent` | `harnessed.events` | Import alias of `CustomTransientEvent`, so a **different class** than core's `TransientEvent` |
| `LogEvent` | `harnessed.events` | Union of built-in durable events |
| `StreamEvent[T]` | `harnessed.events` | Union of built-in transient events |
| `EventStream[T]` | `harnessed.events` | Union of what `stream()` yields |
| `BuiltinEvent` | `harnessed.events` | `LogEvent \| StreamEvent[Any]` |
| `AgentEvent` | `core/runtime/session.py` | `SessionEvent \| TransientEvent` (core meaning) |

Consequences:

- `isinstance(TextDeltaEvent(...), harnessed.events.TransientEvent)` is
  **false**, because the public name points at the custom subclass.
- `isinstance(TurnEndedEvent(...), DurableEvent)` is **false**, because built-in
  durable events subclass `SessionEvent` directly.
- Core code (`plugin.py`, `event_registry.py`, `codec.py`) uses
  `TransientEvent` with the core meaning, while docs use the public meaning.

### Plugin event lives in core

`RuleTriggeredEvent` (`rule.triggered`) is defined in
`core/session/events.py` and exported from `harnessed.events`, but only
`harnessed/plugins/rules.py` emits it. Core thereby knows about one optional
plugin. `EventRegistry` already supports plugin-declared events.

## Target API

Two public bases, both used for built-in and custom events alike:

```python
from dataclasses import dataclass, field
from typing import Literal
from harnessed.events import DurableEvent, TransientEvent

@dataclass(frozen=True)
class DeployFinishedEvent(DurableEvent):        # persisted, replayed, on the stream
    type: Literal["deploy.finished"] = field(default="deploy.finished", kw_only=True)
    env: str

@dataclass(frozen=True)
class DeployLogLineEvent(TransientEvent):       # stream only
    type: Literal["deploy.log.line"] = field(default="deploy.log.line", kw_only=True)
    line: str
```

- `SessionEvent` becomes the internal common root. It is not exported, or
  exported only for `isinstance` checks. See decision 1.
- Built-in durable events subclass `DurableEvent`; built-in transient events
  subclass `TransientEvent`. `isinstance(event, DurableEvent)` is then the
  single "is it in the log?" check.
- `CustomTransientEvent` is removed. There is exactly one `TransientEvent`
  class, and core and the public facade mean the same thing by it.
- `BuiltinEvent` and the core-internal `AgentEvent` are removed (`BuiltinEvent`
  is also listed in ticket 03).
- `EventRegistry` keeps rejecting custom classes that subclass a *concrete*
  built-in event. It distinguishes built-ins by membership in the built-in
  unions, not by base class.

The rules plugin owns its event:

```python
# harnessed/plugins/rules.py
@dataclass(frozen=True)
class RuleTriggeredEvent(DurableEvent):
    type: Literal["rule.triggered"] = field(default="rule.triggered", kw_only=True)
    ...

class Rules(Plugin[RulesConfig]):
    events = (RuleTriggeredEvent,)
```

```python
from harnessed.plugins.rules import RuleTriggeredEvent   # new import path
```

`RuleAction` moves with it. `EventKind.RULE_TRIGGERED` is removed from core.

## Scope

- `core/session/events.py` (or `core/events/base.py` after 01): base hierarchy.
- `core/stream.py`: transient built-ins subclass `TransientEvent`.
- `core/event_registry.py`: today it checks for `DurableEvent | CustomTransientEvent`.
  Switch to checking the base classes plus "is not a built-in type".
- `harnessed/plugins/rules.py`: define and declare `RuleTriggeredEvent`.
- `harnessed/events/__init__.py`: drop `CustomTransientEvent`, `BuiltinEvent`,
  `RuleTriggeredEvent`, and possibly `SessionEvent`.
- Codec: `rule.triggered` is decoded through the plugin registry, so a log
  containing it needs the `Rules` plugin installed to decode. Verify what the
  codec does for unknown tags today and document it.
- Docs that mention the bases: `docs/guides/events.md`, `docs/reference/events.md`.
- `Changelog.md`: breaking entry.

## Acceptance

- Baseline checks pass.
- `git grep -n "CustomTransientEvent\|BuiltinEvent\|AgentEvent"` returns nothing.
- `git grep -n "RuleTriggered" harnessed/core` returns nothing.
- For every built-in event, `isinstance(event, DurableEvent)` matches membership
  in `LogEvent`, and `isinstance(event, TransientEvent)` matches membership in
  `StreamEvent` (parametrised test).
- Round-trip test: a session using `Rules` writes `rule.triggered` and reloads it
  with the plugin installed.

## Decisions

1. Export `SessionEvent` publicly (useful for `isinstance(x, SessionEvent)` in
   generic subscribers), or keep it internal? The ticket recommends keeping it
   exported but documenting only the two bases for authors.
2. Decoding behaviour for plugin events when the plugin is missing: reject (strict,
   current style) or keep an opaque record? The ticket recommends reject, matching
   the codec's strictness.
