# Compact long conversations

Compaction replaces older working history with a complete summary and retains
the last complete model/tool steps unchanged. Exported `SessionState` values
contain this compacted conversation. Store a complete audit externally when
historical records are needed.

```python
from harnessed import Agent
from harnessed.plugins import Compaction, DEFAULT_COMPACTION_PROMPT, at_ratio

agent = Agent(
    model="deepseek:deepseek-chat",
    plugins=[
        Compaction(
            trigger=at_ratio(0.8),
            keep_last_steps=2,
            target_ratio=0.4,
            prompt=DEFAULT_COMPACTION_PROMPT + "\nPreserve relevant file paths.",
        ),
    ],
)
```

## Triggers and retention

`at_ratio(0.8)` is the default trigger. It measures estimated input **plus reserved
output**, divided by the effective context window. Unknown windows require an
explicit `AgentLoop(context_window=...)`. `every_turns(N)` counts newly completed
turns since the last compaction. Custom synchronous triggers receive the frozen
`ContextState(input_tokens, reserve_tokens, context_window, turns_since_compaction)`;
its `ratio` property is `None` without a known window.

`keep_last_steps` is a positive integer, defaulting to two. A step is one model
invocation and its associated tool calls and results. Parallel tool calls count
as one step; text-only responses also count. Retention spans turn boundaries,
including while idle. With seven complete steps and `keep_last_steps=3`, steps
1–4 become a summary and steps 5–7 remain verbatim. Fewer than N complete steps
leave no eligible older prefix.

The active task's inputs, steering and context remain protected, along with
inputs needed to interpret retained steps from earlier turns. An unfinished
step and current observations remain protected in addition to the completed
suffix. Original message identities, tool-call IDs, multimodal content and
provider state are preserved, in order, once each. Completed tools never rerun.
Old summaries become input to the next summary rather than accumulating.

Changing retention cannot recover discarded history. Snapshots retain their
own messages and resource bytes. Completed-step boundaries survive snapshots
and restarts independently of execution-event buffers.

## Prompts and summarization models

`prompt` replaces the summarizer's entire system prompt. Omission selects the
public `DEFAULT_COMPACTION_PROMPT`. A string or sync/async factory may be used;
empty prompts and empty factory results are rejected. A factory is evaluated
once per summarization attempt and receives an immutable `CompactionContext`:

- `history_to_compact`: messages replaced by the summary, including a previous summary.
- `retained_context`: non-instruction messages that remain verbatim.
- `instructions`: effective agent system instructions from the captured request.
- `state`: captured request sizes, output reserve, window and turn count.
- `target_tokens`: resolved explicit whole-input target, or `None`.

The built-in summarizer labels these as separate reference sections. Agent
instructions and historical content remain reference data; only `prompt`
occupies the summarizer's system role. Tools are disabled. Text and captions
are included; images receive an omission marker and are not reanalyzed. Custom
summarizers receive original typed content, including image references.

Use `llm=some_model` or `llm="provider:model"` to select a separate summarization
model. Omission uses the current session model captured for the request. Wrap
native PydanticAI models in `PydanticAIModel` explicitly. Owned model resources
close with the plugin. The built-in path budgets its complete reference request
against the selected model's window and output limits before transport; it
fails without changing history if required references do not fit.

Custom implementations use the public service contracts:

```python
from harnessed.services import CompactionRequest, Summary


class MySummarizer:
    async def summarize(self, request: CompactionRequest) -> Summary:
        # Read request.context and honor request.max_summary_tokens.
        return Summary("A complete continuation handoff.")
```

Pass an implementation with `summarizer=MySummarizer()`. This is mutually
exclusive with explicit `llm` and `prompt`. `Summary(text, usage=...)` reports
incurred `ModelUsage`, including for rejected candidates. A plain string is not
a valid result. `Summarizer` is exported by `harnessed.services`.

## Budgets and targets

Summary generation receives a finite budget derived from available request
space, model output metadata/settings and optional `max_summary_tokens`. There
is no fixed 2,048-token default. If no finite bound is available, `ConfigError`
requests an explicit ceiling. Token measurements use the session counter and
one calibration snapshot throughout an operation. The default counter estimates
UTF-8 bytes / 4 and calibrates from reported provider usage. Supply a tokenizer
through `Service(TokenCounter, counter)` when available.

`target_ratio` or `target_tokens` optionally sets an upper bound for the **entire
input request after compaction**, including fixed instructions and tool/output
schemas. They are mutually exclusive. Ratios require a known effective window
and exclude the output reserve; the hard input ceiling separately remains
`window - reserve`. A target of 0.4 is an application choice, not a default.

`max_summary_tokens` is an optional positive ceiling for summary text measured
with the session counter. Generated text is never sliced to fit. Empty output,
tool calls and provider-truncated output cannot be committed. At most one further
summarization call can request a tighter summary or give a truncated response
more generation room. All incurred usage is recorded. The smallest complete,
valid candidate is retained; an explicit text ceiling remains mandatory.

Targets are best effort. Protected content alone may exceed the target; in that
case the runtime skips a futile target retry and attempts useful compaction
within the hard budget. A smaller candidate may be committed with
`target_reached=False`. The normal context guard still blocks an over-budget
ordinary provider call. Protected inputs and steps are never truncated to fit.

Automatic compaction runs after request hooks and before the provider call.
Non-reducing attempts back off for two more closed turns or two more completed
steps in the active turn. Forced and manual attempts bypass backoff. A provider
`ContextLengthExceeded` before a visible partial response may trigger one smaller
projection and a new step, within the normal step limit. This recovery never
re-executes completed tools and remains limited to one per turn.

## Display compaction activity

`CompactionStartedEvent`, durable `CompactionAppliedEvent`, and
`CompactionFinishedEvent` are exported by `harnessed.events`. Start/finish share
`compaction_id`; a committed record uses the same `id`. Finished outcomes are
`APPLIED`, `DISCARDED`, `FAILED` and `CANCELLED`. Errors and cancellation propagate.
An observer failure after commit still reports `APPLIED` and committed sizes.

All three capture `before_tokens`, `context_window`, `reserve_tokens` and
`target_tokens`. Applied records require integer `after_tokens`. A discarded
finish reports the unchanged input count; failure/cancellation has no after
count unless a projection was committed. Derived properties expose
`before_ratio`, `after_ratio`, `freed_tokens` and `target_reached` wherever their
inputs exist. Started events expose only `before_ratio`.

Occupancy ratios describe input only, are not clamped at one, and remain `None`
without a window. They are request-counter estimates, separate from billed
summarizer usage. For example, a 200,000-token window and input counts of
172,000 → 72,000 produce ratios of 0.86 → 0.36 and `freed_tokens=100_000`.

`session.stream()` forwards these events during a turn. Use
`session.subscribe(transient=True)` for session-wide activity, including manual
compaction while idle or paused. Start/finish are not persisted history.

## Compact manually

`await session.compact()` returns the committed record, or `None` when there is
no eligible prefix or smaller valid projection. It works without a plugin,
using the session model and retaining the latest two complete steps. With a
plugin it uses that plugin's configuration. Pause a running turn first;
compaction preserves pause state. Incoming queued input can arrive during
summarization, but its model step waits until compaction finishes.

Run the [offline policy comparison](examples.md) with
`uv run python -m examples.16_compaction_policies`.
