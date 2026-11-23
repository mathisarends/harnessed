import asyncio

import pytest
from pydantic import BaseModel, field_validator

from harnessed import Agent, Plugin, tool
from harnessed.events import (
    ControlState,
    InboxAddedEvent,
    InboxRemovalReason,
    InboxRemovedEvent,
    SessionControlChangedEvent,
    TurnEndedEvent,
)
from harnessed.hooks import PreStep, StepDecision
from harnessed.kernel import hook
from harnessed.models import ModelChunk, StopReason, UserMessage
from harnessed.runtime.control import SessionStatus
from harnessed.runtime.handles import RunHandle, RunStream
from harnessed.sessions import SessionCommit
from harnessed.testing import CallbackModel, ScriptedModel, call
from tests._audit import Audit
from tests._commits import CommitRecorder


async def test_submitted_handle_has_no_live_iteration_or_observation_to_close() -> None:
    async with Agent(model=ScriptedModel(["done"])).session() as session:
        handle = await session.submit("work")
        assert isinstance(handle, RunHandle) and not isinstance(handle, RunStream)
        with pytest.raises(TypeError, match="not an async iterable"):
            aiter(handle)
        with pytest.raises(AttributeError, match="aclose"):
            await handle.aclose()
        assert (await asyncio.wait_for(handle.result(), timeout=3)).text == "done"


async def test_submit_acknowledges_queue_order_and_results_can_be_awaited_repeatedly():
    model = ScriptedModel(["first", "second"])
    async with Agent(model=model).session() as session:
        await session.pause()
        first = await session.submit("one")
        second = await session.submit("two")
        assert [item.id for item in session.inbox.pending] == [first.id, second.id]
        assert not first.done() and not second.done() and model.requests == []
        await session.resume()
        result = await first.result()
        assert result.text == "first"
        assert await first.result() is result
        assert (await second.result()).text == "second"
        assert first.done() and second.done()


async def test_cancelling_a_result_waiter_leaves_the_acknowledged_run_queued():
    async with Agent(model=ScriptedModel(["done"])).session() as session:
        await session.pause()
        handle = await session.submit("work")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(handle.result(), 0.01)
        assert not handle.done() and session.inbox.pending[0].id == handle.id
        await session.resume()
        assert (await handle.result()).text == "done"


async def test_cancelled_submit_runs_once_after_its_input_commit_succeeds() -> None:
    saving = asyncio.Event()
    release = asyncio.Event()
    store = CommitRecorder()

    async def persist(commit: SessionCommit) -> None:
        if any(isinstance(change.event, InboxAddedEvent) for change in commit.changes):
            saving.set()
            await asyncio.wait_for(release.wait(), timeout=3)
        await store(commit)

    model = ScriptedModel(["done"])
    async with Agent(model=model).session(on_commit=persist) as session:
        submit = asyncio.create_task(session.submit("accepted work"))
        try:
            await asyncio.wait_for(saving.wait(), timeout=3)
            submit.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(submit, timeout=3)
        await asyncio.wait_for(session.control.join(), timeout=3)
        assert session.inbox.pending == ()
        assert len(model.requests) == 1
        checkpoint = store.latest(session.id)
        assert [entry.message.text for entry in checkpoint.state.history] == [
            "accepted work",
            "done",
        ]


@pytest.mark.parametrize("message_id", ["system", "mount-context:1"])
async def test_reserved_input_ids_are_rejected_before_admission(message_id: str) -> None:
    store = CommitRecorder()
    model = ScriptedModel(["done"])
    async with Agent(model=model).session(on_commit=store) as session:
        message = UserMessage("invalid", id=message_id)
        with pytest.raises(ValueError, match="reserved"):
            await session.run(message)
        with pytest.raises(ValueError, match="reserved"):
            await session.append_message(message)
        with pytest.raises(ValueError, match="reserved"):
            session.observe(message, source="test")
        assert store.commits == [] and model.requests == []
        assert session.inbox.pending == () and session.messages == ()

        result = await session.run(UserMessage("valid", id="system-1"))
        assert result.state.history[0].message.id == "system-1"


@pytest.mark.parametrize("plugin_context", [False, True])
async def test_appending_a_queued_id_is_rejected_without_consuming_the_run(
    plugin_context: bool,
) -> None:
    model = ScriptedModel(["done"])
    async with Agent(model=model).session() as session:
        await session.pause()
        message = UserMessage("queued work")
        handle = await session.submit(message)
        before = session.events
        writer = session.plugin_context if plugin_context else session
        with pytest.raises(ValueError, match="queued inbox"):
            await writer.append_message(UserMessage("quiet input", id=message.id))
        assert session.events == before
        assert [item.id for item in session.inbox.pending] == [handle.id]
        assert session.messages == () and model.requests == []
        await session.resume()
        assert (await asyncio.wait_for(handle.result(), timeout=3)).text == "done"
        assert handle.done() and len(model.requests) == 1


async def test_rejected_append_of_the_same_message_keeps_queued_cancellation_available() -> None:
    model = ScriptedModel([])
    async with Agent(model=model).session() as session:
        await session.pause()
        message = UserMessage("queued work")
        handle = await session.submit(message)
        with pytest.raises(ValueError, match="queued inbox"):
            await session.append_message(message)
        await asyncio.wait_for(handle.cancel(), timeout=3)
        result = await asyncio.wait_for(handle.result(), timeout=3)
        assert result.stop_reason is StopReason.DISCARDED
        assert handle.done() and session.inbox.pending == ()
        assert session.messages == () and model.requests == []


async def test_cancelling_one_queued_run_preserves_the_pause_and_other_inputs():
    model = ScriptedModel(["second"])
    async with Agent(model=model).session() as session:
        await session.pause()
        first = await session.submit("one")
        second = await session.submit("two")
        await first.cancel()
        await first.cancel()
        assert (await first.result()).stop_reason is StopReason.DISCARDED
        assert session.status is SessionStatus.PAUSED
        assert [item.id for item in session.inbox.pending] == [second.id]
        assert model.requests == []
        await session.resume()
        assert (await second.result()).text == "second"


async def test_cancelling_an_active_run_preserves_a_reused_input_queued_behind_it():
    started, release = asyncio.Event(), asyncio.Event()
    requests = 0

    async def chunks(request):
        nonlocal requests
        requests += 1
        if requests == 1:
            started.set()
            await release.wait()
        yield ModelChunk(text="second")

    async with Agent(model=CallbackModel(chunks)).session() as session:
        message = UserMessage("reused")
        first = await session.submit(message)
        await asyncio.wait_for(started.wait(), timeout=3)
        second = await session.submit(message)
        assert first.id == second.id
        await asyncio.wait_for(first.cancel(), timeout=3)
        assert (await first.result()).stop_reason is StopReason.ABORTED
        assert (await asyncio.wait_for(second.result(), timeout=3)).text == "second"
        await first.cancel()
        assert requests == 2 and second.done()


async def test_cancelling_the_cancel_waiter_still_finishes_cleanup_and_starts_the_next_run():
    started, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def chunks(request):
        if request.messages[-1].text == "first":
            started.set()
            await asyncio.Event().wait()
        yield ModelChunk(text="second")

    async with Agent(model=CallbackModel(chunks)).session() as session:

        @session.scope.on(SessionControlChangedEvent)
        async def hold_cancellation(event):
            if event.state is ControlState.ABORTING:
                cancelling.set()
                await release.wait()

        first = await session.submit("first")
        await asyncio.wait_for(started.wait(), timeout=3)
        second = await session.submit("second")
        waiter = asyncio.create_task(first.cancel())
        try:
            await asyncio.wait_for(cancelling.wait(), timeout=3)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(waiter, timeout=3)
        finally:
            release.set()
        assert (await asyncio.wait_for(first.result(), timeout=3)).stop_reason is StopReason.ABORTED
        assert (await asyncio.wait_for(second.result(), timeout=3)).text == "second"


async def test_cancellation_racing_with_completion_does_not_abort_the_next_turn(monkeypatch):
    started = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    cancelling, admit_cancellation = asyncio.Event(), asyncio.Event()
    requests = 0

    async def chunks(request):
        nonlocal requests
        index = requests
        requests += 1
        started[index].set()
        await release[index].wait()
        yield ModelChunk(text=f"answer {index}")

    async with Agent(model=CallbackModel(chunks)).session() as session:
        original = session.log._append

        async def append(event, *, during_run=None, expected_turn=None, expected_input=None):
            if expected_turn is not None:
                cancelling.set()
                await admit_cancellation.wait()
            return await original(
                event,
                during_run=during_run,
                expected_turn=expected_turn,
                expected_input=expected_input,
            )

        monkeypatch.setattr(session.log, "_append", append)
        first = await session.submit("first")
        await asyncio.wait_for(started[0].wait(), timeout=3)
        second = await session.submit("second")
        cancel = asyncio.create_task(first.cancel())
        try:
            await asyncio.wait_for(cancelling.wait(), timeout=3)
            release[0].set()
            await asyncio.wait_for(started[1].wait(), timeout=3)
            admit_cancellation.set()
            await asyncio.wait_for(cancel, timeout=3)
            assert not second.done()
        finally:
            admit_cancellation.set()
            for gate in release:
                gate.set()
            await asyncio.wait_for(cancel, timeout=3)
        assert (await first.result()).text == "answer 0"
        assert (await asyncio.wait_for(second.result(), timeout=3)).text == "answer 1"


@pytest.mark.parametrize("discard_second", [False, True])
async def test_reusing_a_consumed_message_does_not_overwrite_a_running_submission(discard_second):
    started, release = asyncio.Event(), asyncio.Event()

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            started.set()
            await release.wait()
            return "done"

    model = ScriptedModel([call(Work.work), "first", "second"])
    async with Agent(model=model, plugins=[Work()]).session() as session:
        message = UserMessage("reused input")
        first = await session.submit(message)
        await asyncio.wait_for(started.wait(), timeout=5)
        second = await session.submit(message)
        assert first is not second and not first.done() and not second.done()
        if discard_second:
            await session.inbox.remove(second.id)
        release.set()
        async with asyncio.timeout(2):
            assert (await first.result()).text == "first"
            result = await second.result()
        if discard_second:
            assert result.stop_reason is StopReason.DISCARDED
        else:
            assert result.text == "second"
        assert first.done() and second.done()


async def test_removing_input_in_an_observer_before_submit_returns_still_completes_handle():
    async with Agent(model=ScriptedModel([])).session() as session:

        @session.scope.on(InboxAddedEvent)
        async def discard(event):
            await session.inbox.remove(event.message.id)

        handle = await session.submit("discarded immediately")
        assert handle.done()
        assert (await handle.result()).stop_reason is StopReason.DISCARDED


@pytest.mark.parametrize("immediate", [False, True])
async def test_stop_and_abort_complete_handles_and_discard_queued_runs(immediate):
    started, release = asyncio.Event(), asyncio.Event()

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            started.set()
            await release.wait()
            return "done"

    async with Agent(model=ScriptedModel([call(Work.work)]), plugins=[Work()]).session() as session:
        running = await session.submit("start")
        await asyncio.wait_for(started.wait(), timeout=5)
        queued = await session.submit("queued")
        if immediate:
            await session.abort()
        else:
            asyncio.get_running_loop().call_soon(release.set)
            await session.stop()
        assert (await running.result()).stop_reason is (
            StopReason.ABORTED if immediate else StopReason.STOPPED
        )
        assert (await queued.result()).stop_reason is StopReason.DISCARDED
        assert session.status is SessionStatus.IDLE


async def test_driver_errors_are_delivered_through_the_run_handle():
    async with Agent(model=ScriptedModel([])).session() as session:
        handle = await session.submit("no response")
        with pytest.raises(RuntimeError, match="ran out"):
            await handle.result()
        assert handle.done()


def _fail_first_call(calls: list[int]) -> CallbackModel:
    async def respond(request):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            await asyncio.sleep(0.05)
            raise RuntimeError("provider down")
        yield ModelChunk(text=f"answer {len(calls)}")

    return CallbackModel(respond)


async def _settle(handle: RunHandle) -> str:
    try:
        return (await handle.result()).text
    except RuntimeError as error:
        return f"raised: {error}"


async def test_driver_failure_discards_queued_runs_its_handles_report_as_failed():
    calls: list[int] = []
    async with Agent(model=_fail_first_call(calls)).session() as session:
        first = await session.submit("first")
        second = await session.submit("second")
        assert await _settle(first) == "raised: provider down"
        assert await _settle(second) == "raised: provider down"
        assert session.inbox.pending == ()
        discarded = [event for event in session.events if isinstance(event, InboxRemovedEvent)]
        assert [(event.message_id, event.reason) for event in discarded] == [
            (second.id, InboxRemovalReason.DISCARDED)
        ]
        third = await session.submit("third")
        assert (await third.result()).text == "answer 2"
        assert calls == [1, 2]
        assert [message.text for message in session.messages if message.role == "user"] == [
            "first",
            "third",
        ]


async def test_failing_turn_end_observer_leaves_no_queued_input_behind():
    class Boom(Plugin):
        @hook(TurnEndedEvent)
        async def boom(self, event: TurnEndedEvent) -> None:
            raise RuntimeError("observer bug")

    model = ScriptedModel(["one", "two"])
    async with Agent(model=model, plugins=[Boom()]).session() as session:
        await session.pause()
        first = await session.submit("first")
        second = await session.submit("second")
        await session.resume()
        assert await _settle(first) == "one"
        assert await _settle(second) == "raised: observer bug"
        assert session.inbox.pending == ()
        assert len(model.requests) == 1


async def test_discarded_queued_runs_reach_on_commit_and_stay_discarded_after_reopening():
    store = CommitRecorder(documents=True)
    agent = Agent(model=_fail_first_call([]))
    async with agent.session(id="failing", on_commit=store) as session:
        await session.submit("first")
        second = await session.submit("second")
        assert await _settle(second) == "raised: provider down"
    assert any(
        isinstance(change.event, InboxRemovedEvent) and change.event.message_id == second.id
        for change in store.changes("failing")
    )
    async with Agent(model=ScriptedModel([])).session(state=store.latest("failing")) as reopened:
        assert reopened.inbox.pending == ()


async def test_rejecting_queued_runs_completes_every_handle_without_another_wakeup():
    async with Agent(model=ScriptedModel([])).session() as session:
        session_audit = Audit(session)
        session.scope.on(PreStep, lambda event, next_: StepDecision.reject())
        await session.pause()
        handles = [await session.submit(str(index)) for index in range(3)]
        await session.resume()
        async with asyncio.timeout(2):
            results = [await handle.result() for handle in handles]
        assert all(result.stop_reason is StopReason.REJECTED for result in results)
        assert not session.inbox.pending and len(session_audit.turns) == 3


async def test_delayed_structured_results_retain_the_original_validated_values():
    validated = []

    class Report(BaseModel):
        value: int

        @field_validator("value")
        @classmethod
        def remember(cls, value):
            validated.append(value)
            return value

    count = 130
    model = ScriptedModel([call("final_answer", value=value) for value in range(count)])
    async with Agent(model=model).session() as session:
        await session.pause()
        handles = [await session.submit(str(value), output=Report) for value in range(count)]
        await session.resume()
        await session.wait_until_idle()
        results = [await handle.result() for handle in handles]
        assert [result.output.value for result in results] == list(range(count))
        assert validated == list(range(count))
