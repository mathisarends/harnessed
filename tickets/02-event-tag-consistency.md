# 02 — Consistent event tags and log format v4

- **Status:** proposed
- **Priority:** high
- **Breaking:** yes. The persisted session log format changes, so readers reject
  version 3 logs.
- **Depends on:** 01 (smaller diff once tags live in `core/events/kinds.py`)

## Problem

AGENTS.md asks for "stable dot-separated event tags". Most tags follow the
pattern `<domain>.<noun?>.<past-tense verb>`, which matches the class name
(`ToolCallRequestedEvent` ↔ `tool.call.requested`). These tags do not:

| Class | Current tag | Problem |
|-------|-------------|---------|
| `TurnStartedEvent` | `turn.start` | Imperative form; class says *started* |
| `TurnEndedEvent` | `turn.end` | Imperative form, and it reads much like `turn.finished` (a different event) |
| `StepStartedEvent` | `step.start` | Imperative form |
| `StepEndedEvent` | `step.end` | Imperative form |
| `ModelUsageRecordedEvent` | `model.usage` | Verb missing |
| `CacheMetricsEvent` | `model.cache` | Neither noun nor verb matches the class |
| `ContextValueChangedEvent` | `context.changed` | `value` missing, so it reads like the whole context changed |
| `AgentControlChangedEvent` | `session.control.changed` | Class says *Agent*, tag says *session* |

There is also semantic overlap that users trip over:

- `TurnEndedEvent` (`turn.end`, durable) records the turn outcome in the log.
- `TurnFinishedEvent` (`turn.finished`, transient) carries the typed
  `RunResult[T]` to stream consumers.

## Target

Tags and class names map 1:1 by rule: `FooBarBazEvent` ↔ `foo.bar.baz`.

| Class (new name where changed) | New tag |
|-------------------------------|---------|
| `TurnStartedEvent` | `turn.started` |
| `TurnEndedEvent` | `turn.ended` |
| `StepStartedEvent` | `step.started` |
| `StepEndedEvent` | `step.ended` |
| `ModelUsageRecordedEvent` | `model.usage.recorded` |
| `ModelCacheMetricsEvent` (was `CacheMetricsEvent`) | `model.cache.metrics` |
| `ContextValueChangedEvent` | `context.value.changed` |
| `SessionControlChangedEvent` (was `AgentControlChangedEvent`) | `session.control.changed` |

`EventKind` member names follow the same rule (`TURN_STARTED`, `STEP_ENDED`,
`MODEL_CACHE_METRICS`, `SESSION_CONTROL_CHANGED`, …).

Add a test so the rule cannot drift:

```python
@pytest.mark.parametrize("event_type", builtin_event_types())  # log + stream union members
def test_event_tag_matches_class_name(event_type: type) -> None:
    assert default_tag(event_type) == tag_from_class_name(event_type.__name__)
```

`builtin_event_types`, `default_tag` and `tag_from_class_name` live in test support as an independent oracle, not in
the library (AGENTS.md: "Keep independent test oracles in test support").

Bump the format version:

```python
# harnessed/core/session/codec.py
CURRENT_VERSION = 4
```

## Scope

- `harnessed/core/event_kinds.py` (or `core/events/kinds.py` after 01).
- Class renames: `CacheMetricsEvent` and `AgentControlChangedEvent`, along with
  their `harnessed.events` exports and `__init__.pyi`, if listed.
- `harnessed/core/session/codec.py`: `CURRENT_VERSION = 4`. The decoder keeps
  rejecting other versions, with no migration (AGENTS.md: no legacy migrations
  for unreleased formats).
- Fixtures: `tests/core/session/fixtures/model_requests.jsonl` and any other
  `.jsonl` fixture that contains old tags. Regenerate them rather than editing by hand.
- Docs: `docs/reference/events.md` (event table), `docs/guides/events.md`, and
  any guide that matches on tags.
- `AGENTS.md`: the example tags in the "Python style and boundaries" section
  (`step.end` → `step.ended`).
- `Changelog.md`: breaking-change entry with an upgrade note covering the
  renamed classes, the renamed tags, and the fact that v3 logs are rejected.

## Steps

1. Add the tag/class-name test. It fails on the eight rows above.
2. Rename the enum members, tags and the two classes. Update the imports.
3. Bump `CURRENT_VERSION` and regenerate the fixtures.
4. Update docs, AGENTS.md and the Changelog.

## Acceptance

- Baseline checks pass.
- The tag/class-name test covers every built-in event type.
- `git grep -nE '"(turn|step)\.(start|end)"|"model\.(usage|cache)"|"context\.changed"'`
  returns nothing in `harnessed/`, `tests/` or `docs/`.
- Decoding a version 3 record fails with the existing version error.

## Out of scope

- Merging or renaming `TurnEndedEvent` / `TurnFinishedEvent`. See the decision below.

## Decisions

1. **`TurnEndedEvent` vs `TurnFinishedEvent`:** keep both, now as
   `turn.ended` (durable outcome) and `turn.finished` (transient result)? An
   alternative is to rename the transient one to `TurnResultEvent` /
   `turn.result`, because it exists to deliver the `RunResult[T]`. The ticket
   recommends `TurnResultEvent`, since "ended" and "finished" are synonyms.
2. Ship this together with ticket 04 so users see only one format bump.
