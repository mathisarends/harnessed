import asyncio

import pytest

from harnessed import Agent, Plugin
from harnessed.core.events.base import TransientEvent
from harnessed.core.events.publishing import SessionPublished
from harnessed.events import (
    CompactionAppliedEvent,
    CompactionFinishedEvent,
    CompactionOutcome,
    CompactionStartedEvent,
)
from harnessed.kernel import hook
from harnessed.plugins import Compaction, every_turns
from harnessed.services import Summary
from harnessed.testing import ScriptedModel
from tests._audit import Audit


class Recorder(Plugin, provides=True):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[TransientEvent] = []

    @hook(SessionPublished)
    def record(self, event: SessionPublished) -> None:
        self.events.append(event.event)


@pytest.mark.parametrize("automatic", [True, False])
@pytest.mark.parametrize(
    "summary,outcome",
    [("short", CompactionOutcome.APPLIED), ("z" * 8000, CompactionOutcome.DISCARDED)],
)
async def test_compaction_lifecycle_is_live_and_correlates_with_the_committed_projection(
    automatic,
    summary,
    outcome,
):
    class Summarizer:
        async def summarize(self, request):
            return Summary(summary)

    recorder = Recorder()
    async with Agent(
        model=ScriptedModel(["first", "retained", "second"]),
        plugins=[
            recorder,
            Compaction(
                trigger=every_turns(2),
                summarizer=Summarizer(),
                keep_last_steps=1,
            ),
        ],
    ).session() as session:
        session_audit = Audit(session)
        assert await session.compact() is None
        assert not session.scope[Recorder].events
        await session.run("x" * 4000)
        await session.run("retained")
        if automatic:
            async with session.stream("current") as run_stream:
                stream = [event async for event in run_stream]
        else:
            await session.compact()
            stream = session.scope[Recorder].events
        started = [e for e in stream if isinstance(e, CompactionStartedEvent)]
        finished = [e for e in stream if isinstance(e, CompactionFinishedEvent)]
        assert len(started) == len(finished) == 1
        assert started[0].compaction_id == finished[0].compaction_id
        assert finished[0].outcome is outcome
        assert finished[0].before_tokens == started[0].before_tokens
        assert finished[0].context_window == started[0].context_window == 128_000
        applied = session.last(CompactionAppliedEvent)
        if outcome is CompactionOutcome.APPLIED:
            assert applied.id == started[0].compaction_id
            assert finished[0].after_tokens == applied.after_tokens
            assert finished[0].freed_tokens == applied.freed_tokens
        else:
            assert applied is None
            assert finished[0].after_tokens == started[0].before_tokens
            assert finished[0].freed_tokens == 0
        assert all(not isinstance(e, TransientEvent) for e in session_audit.events)


@pytest.mark.parametrize("cancel", [False, True])
async def test_compaction_failure_and_cancellation_always_close_the_started_lifecycle(cancel):
    entered = asyncio.Event()

    class Summarizer:
        async def summarize(self, request):
            entered.set()
            if cancel:
                await asyncio.Event().wait()
            raise ValueError("summary failed")

    recorder = Recorder()
    async with Agent(
        model=ScriptedModel(["first", "retained"]),
        plugins=[
            recorder,
            Compaction(
                trigger=every_turns(2),
                summarizer=Summarizer(),
                keep_last_steps=1,
            ),
        ],
    ).session() as session:
        await session.run("x" * 4000)
        await session.run("retained")
        before = session.messages
        task = asyncio.create_task(session.compact())
        await asyncio.wait_for(entered.wait(), 3)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else ValueError):
            await asyncio.wait_for(task, timeout=5)
        started = [
            e for e in session.scope[Recorder].events if isinstance(e, CompactionStartedEvent)
        ]
        finished = [
            e for e in session.scope[Recorder].events if isinstance(e, CompactionFinishedEvent)
        ]
        assert len(started) == len(finished) == 1
        assert finished[0].compaction_id == started[0].compaction_id
        assert finished[0].outcome is (
            CompactionOutcome.CANCELLED if cancel else CompactionOutcome.FAILED
        )
        assert session.messages == before
        assert session.last(CompactionAppliedEvent) is None
        assert finished[0].before_tokens == started[0].before_tokens
        assert finished[0].after_tokens is None
        assert finished[0].freed_tokens is None


async def test_failure_after_durable_apply_still_reports_applied():
    class FailingObserver(Plugin):
        @hook(CompactionAppliedEvent)
        def fail(self, event: CompactionAppliedEvent) -> None:
            raise ValueError("observer failed")

    recorder = Recorder()
    async with Agent(
        model=ScriptedModel(["first", "retained", "summary"]),
        plugins=[recorder, FailingObserver(), Compaction(keep_last_steps=1)],
    ).session() as session:
        await session.run("x" * 4000)
        await session.run("retained")
        with pytest.raises(ValueError, match="observer failed"):
            await session.compact()
        finished = [
            e for e in session.scope[Recorder].events if isinstance(e, CompactionFinishedEvent)
        ]
        assert len(finished) == 1 and finished[0].outcome is CompactionOutcome.APPLIED
        assert session.last(CompactionAppliedEvent).id == finished[0].compaction_id
        assert finished[0].after_tokens == session.last(CompactionAppliedEvent).after_tokens
