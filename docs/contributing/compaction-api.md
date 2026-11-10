# Compaction API proposal

Status: **implemented in the development version**, 2026-11-10. This document
records the original design and rationale, based on `origin/main` at `ac8de54`.
The runtime now implements these contracts. The [compaction guide](../guides/compaction.md)
and [migration notes](../releases.md#0160-2026-11-10) describe the current API.

## TL;DR

- Replace `focus` with a complete, replaceable `prompt`. Omission selects an
  inspectable default; an explicit prompt replaces it rather than appending to it.
- Use `llm=` to select the language model, separately from `summarizer=`
  implementation replacement. The name distinguishes it from a Pydantic output
  schema.
- Give summarization the history to replace, retained context and agent
  instructions as distinct, typed inputs, captured from one request snapshot.
- Use one retention setting, `keep_last_steps`, to append the most recent
  complete model/tool steps verbatim after the summary, across turn boundaries.
  Remove the separate turn-retention and active-turn-step controls.
- Replace the fixed `summary_tokens=2048` policy with an automatically derived
  budget. Offer an optional target for the **whole input request after compaction**
  and an optional `max_summary_tokens` ceiling.
- Report actual before/after request tokens and the effective context window in
  compaction events. A UI can display `86% -> 36%`; this is an observed result,
  independent of any configured target.
- Preserve safe step boundaries, protected inputs, provider-neutral models,
  lifecycle/usage reporting and the existing trigger extension point. Do not add
  compatibility aliases for the removed API.

## Problem on the baseline

`Compaction(focus=...)` appends text to a hidden model-summarization prompt.
Replacing that prompt requires implementing `Summarizer`, even when the
application only wants different wording or a different summary structure.
`summarizer=` also accepts model strings, Harnessed models and native provider
model objects, mixing model selection with algorithm replacement.

The summarizer receives only the history selected for replacement. Current task
inputs, retained steps and the agent's instructions remain in the agent request,
but are unavailable to summarization as orientation. `summary_tokens=2048` is both
a model-generation limit and a post-generation text limit. The latter silently
cuts text at the end. A result is accepted whenever the projected request is
smaller, even by one token; there is no optional post-compaction context target.

`CompactionAppliedEvent` already has `before_tokens` and `after_tokens` for the
complete input requests. `CompactionStartedEvent` and `CompactionFinishedEvent`
carry no sizes. None of these events captures the effective context limit needed
to interpret those sizes as percentages later.

## Developer-facing API

The application-supplied `main_model` and `summary_llm` below implement
`harnessed.models.Model`. Every new argument and export in these examples is
part of this proposal.

```python
from harnessed import Agent
from harnessed.plugins import Compaction, at_ratio

agent = Agent(
    model=main_model,
    plugins=[
        Compaction(
            llm=summary_llm,
            prompt="""
                Write a handoff for continuing the user's task.
                Preserve requirements, verified findings with their sources,
                decisions, failed approaches, uncertainty and unfinished work.
                Distinguish historical observations from current state.
            """,
            trigger=at_ratio(0.8),
            target_ratio=0.4,
            keep_last_steps=2,
        ),
    ],
)
```

Here `0.4` is an example upper bound for the complete input request, not a desired
summary length, a requirement to fill the budget, or a researched optimal default.
An absolute `target_tokens=40_000` is the alternative for applications with a
fixed input budget.

The proposed constructor replaces the old retention controls with
`keep_last_steps=2`. Its parameters are:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `llm: Model \| str \| None` | `None` | Select the language model that writes the summary, not a structured-output schema. Omission uses the current session model. Wrap native PydanticAI models explicitly with `PydanticAIModel`. |
| `prompt: CompactionPrompt \| None` | `None` | Complete summarization system prompt or a per-attempt factory; omission uses `DEFAULT_COMPACTION_PROMPT`. |
| `summarizer: Summarizer \| None` | `None` | Replace the summarization implementation. Mutually exclusive with explicit `llm` and `prompt`. |
| `keep_last_steps: int` | `2` | Keep the last N complete model/tool steps verbatim after the summary, across the currently available history; a positive integer. |
| `target_ratio: float \| None` | `None` | Optional post-compaction input ceiling as a fraction of the effective context window; finite and in `(0, 1]`. |
| `target_tokens: int \| None` | `None` | Optional positive post-compaction input ceiling; mutually exclusive with `target_ratio`. |
| `max_summary_tokens: int \| None` | `None` | Optional positive ceiling for summary text, measured by the session token counter. Also applies to custom summarizers. |

`trigger=at_ratio(0.8)` remains the default. Trigger ratios retain their existing
meaning: estimated input **plus reserved output**, divided by the effective
context window. Target ratios and event occupancy ratios describe **input only**.
The output reserve is still enforced separately by the hard context budget.
These meanings must be explicit in constructor and event documentation.

Omitting both target arguments requests an automatically bounded, concise
summary without imposing an arbitrary post-compaction percentage. There is no
replacement fixed 2,048-token default. The implementation must derive a finite
generation limit from available context, model output metadata/settings and any
explicit ceiling. If those provide no finite limit, raise a named configuration
error requesting an explicit ceiling; never generate without a bound.

Unknown context windows require explicit configuration for ratio-based triggers
or targets. Absolute token targets and custom triggers remain available. Do not
introduce a global 100k fallback. Browserrig can retain its own `WindowRatio()`
policy, including its fallback, by passing that trigger explicitly.

### Retention: append the last complete steps unchanged

`keep_last_steps=N` preserves the N most recent completed steps from the
currently available projected history. A **step** is one model invocation and
its associated tool calls and results, if any. Several parallel tool calls from
one response belong to one step. Text-only model responses also count as steps;
individual tool calls, messages and streamed chunks are not separate steps.

The setting applies across turn boundaries, both during a running task and while
idle. It does not preserve whole completed tasks. If the current task has only
one completed step and N is three, retain that step and the last two available
steps from the preceding task. If fewer than N steps exist, retain all of them;
compaction may have no eligible older history.

For seven completed steps with `keep_last_steps=3`:

```text
before:  [steps 1-4                         ][step 5][step 6][step 7]
after:   [summary of older eligible history][step 5][step 6][step 7]
                                            unchanged original messages
```

The summary replaces only eligible older history. Reattach the retained suffix
in its original order, with original message identities, tool-call IDs, results,
multimodal content and provider state; include each message once. This changes
the next model request, not tool execution: completed tools are never rerun.
The retained steps are reference context for summarization, not history to
replace, so the summary need not repeat their detailed tool output.

The active task and its steering/context inputs remain protected regardless of
age. Also retain task/steering inputs needed to interpret steps kept from an
earlier turn, placing them before the affected steps. Fixed instructions and
tool schemas stay in their normal request roles. An unfinished step and current
observations remain protected in addition to the N completed steps; never split
a tool call from its results to meet the count or a token target.

The count protects complete steps, not a fixed number of tokens. One large tool
result can make the retained suffix exceed a size target; report that miss
instead of truncating or silently reducing N. The existing `keep_turns`,
`keep_steps`, `keep_first_turns` and `preserve_turns` arguments are removed from
the proposed constructor, without aliases. Turn-based pinning and token-based
retention are separate future requirements, not additional controls in this API.

### Prompt replacement and runtime context

Export `CompactionPrompt` and `DEFAULT_COMPACTION_PROMPT` from
`harnessed.plugins`, and the request/protocol types below from
`harnessed.services`. Their implementation belongs to core, with explicit facade
re-exports; optional features must not depend on another plugin.

```python
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from harnessed.models import Message, SystemMessage
from harnessed.plugins import ContextState


@dataclass(frozen=True)
class CompactionContext:
    history_to_compact: tuple[Message, ...]
    retained_context: tuple[Message, ...]
    instructions: tuple[SystemMessage, ...]
    state: ContextState
    target_tokens: int | None


type CompactionPrompt = str | Callable[[CompactionContext], str | Awaitable[str]]
```

`history_to_compact` includes the previous summary when applicable.
`retained_context` contains the non-instruction messages that remain verbatim,
including active task inputs and recent steps. `instructions` captures the
effective agent instructions from the request being compacted. Preserve message
order and identity within each input. Do not invent an inferred `current_task`
field: Harnessed does not currently own a canonical task object.

The built-in summarizer presents these as separately labeled reference data.
Only `history_to_compact` is replaced by its output. Agent instructions and
historical messages are not installed as the summarizer's system instructions.
The explicit prompt occupies that role. Tool access remains disabled regardless
of prompt content. No `{messages}` placeholder or implicit `Focus:` suffix is
required. Reject empty explicit prompts and empty factory results.

Evaluate a prompt factory once per actual summarization attempt, using the same
immutable snapshot as budgeting. A bounded retry may evaluate it again with the
new attempt's budget. Factories receive no session controller, DI scope or live
mutable history. Applications can close over their own state when necessary.

The default prompt should request a concise continuation handoff covering goals,
constraints, decisions, verified results and sources, failures, uncertainties
and next actions. It should distinguish observations from currently verified
state and avoid promoting quoted content into instructions. Its text is public,
so applications can deliberately extend it or replace its output structure.

The structured inputs preserve typed message content for custom summarizers.
The built-in path must document its text/image treatment and bound its own
request, including retained reference context, before calling the provider. It
must not silently drop task inputs or instructions to fit a smaller summarizer
window. A configured model that cannot fit the required inputs fails without
committing a history change. Domain-specific browser state can be supplied by
the application; no Browserrig dependency belongs in core.

### Custom summarizers

Replace `summarize(messages, *, max_tokens) -> str | Summary` with one typed
request and one result type. The existing `Summary(text, usage=None)` remains the
result contract; report available usage even for rejected output.

```python
from dataclasses import dataclass
from typing import Protocol

from harnessed.services import CompactionContext, Summary


@dataclass(frozen=True)
class CompactionRequest:
    context: CompactionContext
    max_summary_tokens: int


class Summarizer(Protocol):
    async def summarize(self, request: CompactionRequest) -> Summary: ...
```

The request's ceiling is resolved by the runtime, using the session's token
counter. A custom implementation owns its prompt and generation logic. A model
name passed to `summarizer=` becomes a configuration error. Keep the current
resource ownership, cancellation and compaction usage accounting contracts.

## Budget and acceptance behavior

Let `W` be the effective context limit, `R` the reserved output and `B` the input
request containing fixed instructions, tool/output schemas, protected messages
and retained context, before adding the new summary. All input measurements use
one counter/calibration snapshot, including framing.

1. Resolve an explicit input target from `floor(W * target_ratio)` or
   `target_tokens`, if configured. Its value is recorded separately from the hard
   input ceiling `W - R`; the effective working ceiling is the lower of the two.
2. Estimate the space available for summary text after accounting for `B` and
   summary framing. Also honor `max_summary_tokens` and the summarizer model's
   own input/output limits. Tokenization differs across models, and reasoning can
   consume generation tokens: a provider `max_tokens` setting is not proof that
   the resulting text fits the session counter's budget.
3. Generate a concise, complete summary. Reject empty results, tool calls and
   provider-truncated output. Never cut the summary's text at the end to make it
   fit. Count the fully assembled candidate request again.
4. Permit at most one further summarization call per compaction operation when
   a usable candidate misses a feasible target or a text-size ceiling. Supply
   the tighter budget explicitly. Keep the best complete candidate; count every
   incurred model usage. Do not loop until the target is reached.
5. Commit only a smaller, valid projection. A target is best effort: the best
   candidate may be committed with `target_reached=False`. The hard context limit
   remains mandatory before the next ordinary model call. If it still cannot
   fit, surface the existing context-budget error rather than calling a provider
   with an over-budget request.

If protected content alone exceeds the target, skip a futile target retry and
attempt useful compaction within the available hard budget. Report the missed
target. If there is no room for any valid summary, do not remove protected
content; fail/discard according to the existing hard-budget behavior.

A generation-length stop is not a complete candidate to truncate or publish.
The single additional call may use a larger generation allowance, if feasible,
while requesting a shorter complete summary. After the bounded attempts are
exhausted, discard when no complete valid candidate exists. Cancellation and
provider errors propagate under the existing lifecycle contract.

Without an explicit target, reduction remains the acceptance condition. Preserve
existing automatic backoff and forced/manual attempts; target misses must not
create an immediate retry loop. Choosing an automatic occupancy target or a
minimum savings threshold requires workload measurements and is not settled by
this proposal.

## Compaction events

Keep the existing started/applied/finished lifecycle and IDs. Add measurements to
its public events rather than requiring a UI to inspect runtime services or
correlate a later model-usage event.

| Field | Started | Applied | Finished |
| --- | --- | --- | --- |
| `before_tokens: int` | Input snapshot before summarization | Same snapshot | Same snapshot |
| `after_tokens: int \| None` | Not present | Committed projection count, always available | Committed count for `APPLIED`; original count for `DISCARDED`; `None` on failure/cancellation without a committed projection |
| `context_window: int \| None` | Captured effective limit | Same limit | Same limit |
| `reserve_tokens: int` | Captured output reserve | Same reserve | Same reserve |
| `target_tokens: int \| None` | Resolved explicit input target | Same target | Same target |

`Applied.after_tokens` is a required integer in its proposed constructor; its
annotation need not include `None`. Event fields are captured values, not a
promise to recount a session that changes after the snapshot. If an observer
fails after commit, the finished outcome and metrics still describe the committed
`APPLIED` projection. Do not report an uncommitted candidate as reclaimed space.

Derived, read-only properties avoid storing redundant values:

- `before_ratio = before_tokens / context_window`, or `None` without a window.
- `after_ratio = after_tokens / context_window`, or `None` without both values.
- `freed_tokens = before_tokens - after_tokens`, or `None` without a result.
- `target_reached = after_tokens <= target_tokens`, or `None` without both values.

Expose each property only where its input fields exist; the started event has
`before_ratio` but no after/reclamation properties.

Ratios are fractions, not percentages, and are not clamped at one. These are
configured token-counter measurements, often estimates, of the **entire input
request**, including system instructions and tool/output schemas. They are not
the summarizer's billed input/output tokens. The output reserve is not added to
occupancy ratios; it remains separately available for budget interpretation.

For example, with a 200,000-token effective window:

```text
before_tokens = 172000
after_tokens = 72000
before_ratio = 0.86
after_ratio = 0.36
freed_tokens = 100000
```

This supports “Compacted 86% -> 36%; freed 100,000 tokens” even when no target
was configured. A configured target does not replace these actual measurements.

## Rationale and current practice

Applications usually care about space for subsequent work and continuity of the
task. A fixed summary length does not express either well across context windows
and retention policies. A whole-request target accounts for protected content,
while an optional text ceiling remains useful for cost and latency control.
Separating those controls also makes target misses explainable.

Public APIs show related patterns, not one universal post-compaction percentage:

| System | Documented controls and relevance |
| --- | --- |
| [OpenAI](https://developers.openai.com/api/docs/guides/compaction) | A trigger threshold and a returned compacted context, including opaque state. The documented flow does not expose a text-summary length or final occupancy target. |
| [Anthropic](https://platform.claude.com/docs/en/build-with-claude/compaction-threshold) | A token trigger and a replaceable summarization prompt. Its threshold-compaction parameters do not specify a final occupancy target. |
| [OpenCode](https://opencode.ai/docs/compaction/) | A free-space buffer for triggering and token-based recent-context retention. The buffer controls when compaction starts, not a guaranteed final size. |
| [Pi](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md) | A reserve for triggering and recent-token retention; summary generation uses available model output capacity rather than a fixed public summary-length setting. |

These sources were reviewed on 2026-11-10. The optional target here is a Harnessed
design choice, not a claim that other harnesses guarantee the same target.
Provider-native encrypted compaction is a different integration concern; this
proposal retains Harnessed's provider-neutral summarizer contract.

Complete prompt replacement makes application intent inspectable. Separating
`llm` and `summarizer` avoids silently ignored knobs. Typed reference context
improves relevance without promoting the agent's entire operational prompt into
the summarizer's instructions. Explicit metrics let frontends display results
without depending on internals or counting tokens themselves.

## Breaking changes and migration

| Current usage | Proposed replacement |
| --- | --- |
| `focus="Keep URLs"` | `prompt=DEFAULT_COMPACTION_PROMPT + "\nPreserve relevant source URLs."`, or a complete custom prompt |
| `summarizer=model_or_name` | `llm=model_or_name` |
| `summarizer=native_provider_model` | `llm=PydanticAIModel(native_provider_model)` |
| `summary_tokens=2048` | Omit for automatic budgeting; use `max_summary_tokens=2048` for an intentional text ceiling, or a target for whole-request sizing |
| `keep_steps=N` with optional `keep_turns`, `keep_first_turns` or `preserve_turns` | `keep_last_steps=N` preserves a suffix across turns; remove turn-based controls. This is a semantic change, not a direct rename of active-turn-only `keep_steps`. |
| `summarize(messages, *, max_tokens)` returning text | `summarize(request)` returning `Summary` |
| Optional token counts in a successful applied event | Required captured counts, plus window, reserve and target metadata |

There are no transitional argument aliases. Update persisted event codecs and
the explicit session-state format version in the implementation change; do not
add legacy migrations for unreleased formats. Update the current guide and SDK
reference only when the runtime actually implements the new contracts.

An explicit `Compaction(...)` still replaces the inherited plugin in its slot;
it does not merge individual fields with Browserrig's recipe. Applications must
retain their custom trigger explicitly. A general recipe override API is outside
this change and must not be introduced as hidden compaction behavior.

## Implementation handoff and acceptance criteria

- [x] Implement the constructor validation, explicit facade exports and typed
  prompt/summarizer contracts; preserve the lazy SDK and standalone kernel.
- [x] Verify literal and sync/async factory prompts replace the default; task
  inputs and agent instructions reach the summarizer in their reference roles.
- [x] Verify separate-model and custom-summarizer paths, contradictory argument
  errors, optional dependencies, resource ownership and incurred usage.
- [x] Test ratio/absolute targets, output reserve, unknown windows, retained-only
  target overflow, different tokenizers and bounded retries with independent
  token-count expectations. Neither target nor ceiling may silently truncate.
- [x] Test event sizes and derived properties on applied, discarded, failed and
  cancelled attempts, including observer failure after commit and concurrent
  incoming context. Check the entire request, not summary text alone.
- [x] Preserve tool-call/result pairs, active task/steering inputs, retained
  multimodal content, repeated compaction, manual/pause/restart behavior and
  context-limit recovery. No removed history may be reconstructed by retention.
- [x] Test `keep_last_steps` across turn boundaries and while idle, fewer than N
  steps, parallel calls within one step, text-only steps, an unfinished step,
  required task inputs and a retained suffix larger than the size target. Check
  original identities/order, one copy per message and no repeated tool effects.
- [x] Bump the state format as needed and update the guide, SDK/event reference,
  consumer typing contracts and offline examples alongside implementation.
- [x] Run the repository's Smelt, Ruff, formatting, Pylint, ty, strict docs build
  and relevant compaction/runtime tests. Measure quality, savings and latency on
  coding and browser workloads before choosing any automatic occupancy default.

Deferred features: semantic triggers, browser-specific state schemas, provider
native compaction, background summarization, mandatory structured summaries,
semantic validators, token-based retention, turn-based pinning and generic
recipe-field overrides.
These can build on the explicit contracts after concrete consumer requirements
are established.
