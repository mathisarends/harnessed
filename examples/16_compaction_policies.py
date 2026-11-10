"""Run with: uv run python -m examples.16_compaction_policies"""

import asyncio

from harnessed import (
    Agent,
    SessionState,
)
from harnessed.errors import ContextBudgetExceeded
from harnessed.events import CompactionAppliedEvent
from harnessed.models import GenerationSettings
from harnessed.plugins import AgentLoop, Compaction, at_ratio, every_turns
from harnessed.testing import ScriptedModel


async def compare(name, trigger):
    model = ScriptedModel(
        [f"answer {index}" for index in range(6)], settings=GenerationSettings(max_tokens=256)
    )
    summaries = ScriptedModel(["Preserve the public API and finish the open work."] * 6)
    app = Agent(
        model=model,
        instructions="Keep the goal and recent work accurate.",
        plugins=[
            AgentLoop(context_window=2500),
            Compaction(
                trigger=trigger,
                llm=summaries,
                keep_last_steps=1,
                max_summary_tokens=48,
            ),
        ],
    )
    async with app.session() as session:
        await session.run("GOAL: keep the public API stable. " + "detail " * 240)
        applied = []
        session.on(CompactionAppliedEvent, applied.append)
        for index in range(1, 6):
            await session.run(f"task {index}: " + "detail " * 240)
        print(f"{name}: {len(applied)} compactions")
        for event in applied:
            print(
                f"  {event.before_tokens} -> {event.after_tokens} estimated input tokens; "
                f"freed {event.freed_tokens} tokens"
            )
        assert applied and event.after_tokens < event.before_tokens
        assert SessionState.from_json(session.snapshot().to_json()) == session.snapshot()
        print("  Recent steps retained; compacted state round trip verified.")


async def main():
    await compare("threshold", at_ratio(0.6))
    await compare("window", every_turns(1))
    await compare("periodic", every_turns(3))

    # Manual compaction is a core operation and uses the session model by default.
    model = ScriptedModel(["first", "second", "third", "Earlier work summarized."])
    async with Agent(model=model).session() as session:
        for index in range(3):
            await session.run(f"task {index}: " + "detail " * 240)
        await session.pause()
        applied = await session.compact()
        assert applied is not None and session.status == "paused"
        print(f"Manual: {applied.before_tokens} -> {applied.after_tokens} tokens, still paused.")
        await session.resume()

    # The current task and tool schemas are never silently removed to fit a budget.
    model = ScriptedModel([])
    async with Agent(
        model=model, plugins=[AgentLoop(context_window=100), Compaction()]
    ).session() as session:
        try:
            await session.run("Current task that cannot be summarized away: " + "x" * 1000)
        except ContextBudgetExceeded as error:
            print(f"Guard stopped the request before calling the provider: {error}")
        assert model.requests == []


if __name__ == "__main__":
    asyncio.run(main())
