# Structured answers

Pass a Pydantic model to `run(output=...)` when the application needs a typed answer.
Both `Agent.run()` and `Session.run()` return `RunResult[T]`.
Call `result.require_output()` to require a completed, validated answer of type `T`.

```python
from typing import Literal
from pydantic import BaseModel
from harnessed import Agent


class Triage(BaseModel):
    severity: Literal["low", "high", "critical"]
    owner: str
    summary: str


agent = Agent(model="openai:gpt-4.1-mini")


async def triage(report: str) -> Triage:
    result = await agent.run(report, output=Triage)
    return result.require_output()
```

The PydanticAI adapter selects the provider's native JSON-schema channel when its
model profile supports it. Otherwise the request exposes a reserved `final_answer`
output tool. This control tool never executes application code, requests approval,
or appears in `result.tool_calls`. Ordinary tools retain their policies and lifetimes.
An answer sent alongside ordinary tool calls is rejected; those tools still run once.

Pydantic validates every proposed answer. The default loop permits two correction
requests; configure `AgentLoop(output_retries=1)` to permit one. The normal
`max_steps` limit still applies. Exhausting validation retries raises
`OutputValidationError` from `harnessed.errors`. Validation feedback names
invalid fields without echoing their submitted values.

Scalar and list outputs use an object envelope (`{"output": ...}`) on the wire.
An unstructured run has `result.output is None`. Stopping, aborting, discarding or
reaching a limit can leave a structured run without a validated answer; reading
its `output` then raises `OutputUnavailable`. `require_output()` also rejects any
incomplete turn, naming its `stop_reason` in the exception. Inspect `result.text`
and `stop_reason` when the caller permits partial work. Completed unstructured
runs return None from both output accessors.

## Output contracts and conversation state

Each turn receives its output type or an explicit versioned `OutputContract`.
`OutputValidatedEvent` records accepted JSON and `OutputValidationFailedEvent`
records correction attempts. Neither imports or serializes Python code. Live
results reuse the validated object so custom validators run once.

```python
from harnessed import OutputContract

triage_output = OutputContract("support.triage.v1", Triage)
result = await agent.run(report, state=state, output=triage_output)
await repository.save(conversation_id, result.state.to_json())
```

Change the key when validation semantics change, including custom validators that
leave JSON Schema unchanged. A registry matches validator identity and schema;
equal schemas do not substitute validators. `output=Triage` and generic types
such as `list[int]` remain supported. Supply the output type again for each turn.
Conversation snapshots contain no pending outputs or Python validator registry;
paused execution restoration is outside their contract.

Run the offline [structured-output example](examples.md#structured-output) to see
an invalid answer corrected through the tool fallback without provider credentials.

## Apply the contract only after work finishes

A completion plugin may allow standalone progress text before an explicit
completion tool. Import `PrepareOutput`, `OutputDecision` and `Next` from
`harnessed.hooks`. In that plugin, derive completion from the current turn's
successful tool receipts so it also works after restoring paused execution:

```python
@hook(PrepareOutput)
async def prepare_output(self, event: PrepareOutput, next_: Next[OutputDecision]) -> OutputDecision:
    finished = any(
        isinstance(item, ToolCallFinishedEvent)
        and item.call.name == "done"
        and item.result.status is ToolStatus.OK
        for item in event.session.log.current_turn().events
    )
    return await next_() if finished else OutputDecision.DEFER
```

`ToolCallFinishedEvent` is public in `harnessed.events`; `ToolStatus` and `hook`
come from `harnessed`. `next_()` preserves other policies; the default is
`OutputDecision.ENFORCE`. The hook receives the assembled request and the declared
schema, after `PrepareRequest` and before output preparation and token checks.
`DEFER` withholds both native JSON constraints and the reserved `final_answer`
tool. Validation uses the effective request schema, so progress text does not
consume correction attempts. Once enforced, parsing and correction limits are
unchanged. The declared turn contract remains available for checkpoint restoration.

This hook only controls output constraints. Use `TurnStopping` to continue after
progress text and ordinary tool-policy hooks for application-specific phase
rules. All steps consume the same token and step budgets. A reserved final-summary
step bypasses `PrepareOutput`, remains unstructured and reports incomplete work.
Listeners are owned by their mounting scope; no process-wide handler replacement
or session registry is needed.
