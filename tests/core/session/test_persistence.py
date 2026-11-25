import asyncio

import pytest
from pydantic import BaseModel

from harnessed import Agent, OutputContract, Plugin, tool
from harnessed.errors import (
    CommitTimeout,
    ConfigError,
    SessionClosed,
    SessionFormatError,
    SessionStateError,
)
from harnessed.events import ApprovalRequestedEvent, MessageAppendedEvent
from harnessed.models import ApprovalResponse, Decision, ImageReference, ToolCall, UserMessage
from harnessed.plugins import Spill
from harnessed.plugins.spill import SpillStore
from harnessed.runtime.control import SessionStatus
from harnessed.sessions import CheckpointRequest, SessionCheckpoint, SessionCommit, SessionState
from harnessed.sessions.values import PluginValues
from harnessed.testing import ScriptedModel, call
from tests._commits import CommitRecorder


@pytest.fixture(params=["typed", "documents"])
def recorder(request) -> CommitRecorder:
    return CommitRecorder(documents=request.param == "documents")


async def test_commits_reopen_and_save_context_and_results_before_return(recorder):
    model = ScriptedModel(["first", "second"])
    agent = Agent(model=model, instructions="Current instructions.")
    async with agent.session(id="tenant:customer/4711", on_commit=recorder) as session:
        await session.append_message(UserMessage("The customer is Ada."))
        assert recorder.latest(session.id).state.history[0].message.text == ("The customer is Ada.")
        result = await session.run("Remember this.")
        assert recorder.latest(session.id).state.history[-1].message.text == "first"
        assert result.state.history[0].message.text == "The customer is Ada."
    assert recorder.session_ids() == ["tenant:customer/4711"]
    async with agent.session(state=recorder.latest("tenant:customer/4711")) as restored:
        assert restored.model_request_count == 0
        assert restored.messages[0].text == "The customer is Ada."
        await restored.run("Continue.")
    assert [message.text for message in model.requests[-1].messages] == [
        "Current instructions.",
        "The customer is Ada.",
        "Remember this.",
        "first",
        "Continue.",
    ]


async def test_commit_changes_are_contiguous_across_deliveries(recorder):
    agent = Agent(model=ScriptedModel(["one", "two"]))
    async with agent.session(on_commit=recorder) as session:
        await session.run("First")
        await session.run("Second")
        session_id = session.id
    sequences = [entry.sequence for entry in recorder.entries(session_id)]
    assert sequences == list(range(len(sequences)))
    assert all(commit.session_id == session_id for commit in recorder.commits)


async def test_typed_handler_does_not_require_checkpoint_json_encoding(monkeypatch):
    def forbidden(_self: SessionCheckpoint) -> str:
        raise AssertionError("The runtime must not require JSON for a typed handler")

    commits: list[SessionCommit] = []

    async def keep(commit: SessionCommit) -> None:
        commits.append(commit)

    monkeypatch.setattr(SessionCheckpoint, "to_json", forbidden)
    agent = Agent(model=ScriptedModel(["done"]))
    async with agent.session(on_commit=keep) as session:
        await session.run(UserMessage("Hello", id="application-message"))
    boundary = commits[-1].boundary
    assert boundary is not None
    checkpoint = SessionCheckpoint.from_journal(
        boundary, [entry for commit in commits for entry in commit.journal]
    )
    async with agent.session(state=checkpoint) as restored:
        assert restored.messages[0].id == "application-message"
        assert restored.messages[-1].text == "done"


async def test_session_checkpoint_captures_a_paused_turn_without_a_handler():
    entered, release = asyncio.Event(), asyncio.Event()

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            entered.set()
            await release.wait()
            return "done"

    model = ScriptedModel([call(Work.work), "finished"])
    agent = Agent(model=model, plugins=[Work()])
    async with agent.session() as session:
        await session.submit("Work")
        await asyncio.wait_for(entered.wait(), timeout=5)
        pausing = asyncio.create_task(session.pause())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(pausing, timeout=5)
        checkpoint = SessionCheckpoint.from_json(session.checkpoint().to_json())
    async with agent.session(state=checkpoint) as restored:
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert restored.messages[-1].text == "finished"
    assert len(model.requests) == 2


async def test_paused_turn_reopens_without_repeating_tools_and_keeps_limits(recorder):
    entered = asyncio.Event()
    release = asyncio.Event()
    executions: list[str] = []

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            executions.append("work")
            entered.set()
            await release.wait()
            return "done"

    model = ScriptedModel([call(Work.work), "finished"])
    agent = Agent(model=model, plugins=[Work()])
    async with agent.session(id="paused", on_commit=recorder) as session:
        handle = await session.submit("Work", max_steps=2)
        await asyncio.wait_for(entered.wait(), timeout=5)
        pausing = asyncio.create_task(session.pause())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(pausing, timeout=5)
        turn = session.log.open_turn
        assert turn is not None and len(turn.steps) == 1
        turn_id = turn.id
    with pytest.raises(SessionClosed):
        await handle.result()
    async with agent.session(state=recorder.latest("paused")) as restored:
        assert restored.status is SessionStatus.PAUSED
        assert restored.model_request_count == 1
        assert restored.log.open_turn.id == turn_id
        assert restored.log.open_turn.start.limits.max_steps == 2
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert restored.messages[-1].text == "finished"
        assert restored.model_request_count == 2
    assert executions == ["work"]
    assert len(model.requests) == 2


async def test_paused_inbox_and_quiet_context_survive_reopening(recorder):
    agent = Agent(model=ScriptedModel(["one", "two"]))
    async with agent.session(id="inbox", on_commit=recorder) as session:
        await session.pause()
    async with agent.session(id="inbox", state=recorder.latest("inbox"), on_commit=recorder) as (
        session
    ):
        assert session.status is SessionStatus.PAUSED
        await session.append_message(UserMessage("Status: ready"))
        await session.submit("First")
        await session.submit("Second")
    async with agent.session(state=recorder.latest("inbox")) as restored:
        assert restored.status is SessionStatus.PAUSED
        assert restored.messages[0].text == "Status: ready"
        assert [item.content.text for item in restored.inbox.pending] == ["First", "Second"]
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert [message.text for message in restored.messages][-4:] == [
            "First",
            "one",
            "Second",
            "two",
        ]


async def test_rejected_append_delivers_no_commit(recorder) -> None:
    model = ScriptedModel(["done"])
    agent = Agent(model=model)
    message = UserMessage("queued work")
    async with agent.session(id="append-collision", on_commit=recorder) as session:
        await session.pause()
        await session.submit(message)
        await session.flush()
        delivered = len(recorder.commits)
        with pytest.raises(ValueError, match="queued inbox"):
            await session.emit(MessageAppendedEvent(message))
        assert len(recorder.commits) == delivered
        assert session.messages == ()
    async with agent.session(state=recorder.latest("append-collision")) as restored:
        assert [item.id for item in restored.inbox.pending] == [message.id]
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert [item.text for item in restored.messages] == ["queued work", "done"]
        assert len(model.requests) == 1


@pytest.mark.parametrize("mode", ["run", "stream"])
async def test_storage_failure_reaches_the_run_caller_without_returning_success(mode):
    recorder = CommitRecorder()

    def fail_terminal(commit: SessionCommit) -> None:
        boundary = commit.boundary
        history = boundary.state.history if boundary is not None else ()
        if history and history[-1].message.text == "done":
            raise OSError("storage unavailable")

    recorder.before = fail_terminal
    async with Agent(model=ScriptedModel(["done"])).session(on_commit=recorder) as session:
        with pytest.raises(OSError, match="storage unavailable"):
            if mode == "run":
                await session.run("Work")
            else:
                async with session.stream("Work") as run_stream:
                    async for _event in run_stream:
                        pass


async def test_failed_commit_rejects_later_admissions_until_reopened():
    recorder = CommitRecorder()
    failures = [OSError("storage unavailable")]

    def fail_once(_commit: SessionCommit) -> None:
        if failures:
            raise failures.pop()

    async with Agent(model=ScriptedModel([])).session(on_commit=recorder) as session:
        await session.append_message(UserMessage("first"))
        recorder.before = fail_once
        with pytest.raises(OSError, match="storage unavailable"):
            await session.append_message(UserMessage("lost"))
        with pytest.raises(OSError, match="storage unavailable"):
            await session.append_message(UserMessage("rejected"))
        session_id = session.id
    async with agent_session(recorder.latest(session_id), recorder) as restored:
        await restored.append_message(UserMessage("second"))
        assert [message.text for message in restored.messages] == ["first", "second"]


@pytest.mark.parametrize("operation", ["flush", "append_message"])
async def test_handler_writing_to_its_own_session_fails_the_commit(operation):
    holder: list = []
    seen: list[BaseException] = []

    async def handler(commit: SessionCommit) -> None:
        if not holder:
            return
        session = holder[0]
        assert session.checkpoint().session_id == commit.session_id
        try:
            if operation == "flush":
                await session.flush()
            else:
                await session.append_message(UserMessage("nested"))
        except SessionStateError as error:
            seen.append(error)
            raise

    async with Agent(model=ScriptedModel(["hi"])).session(on_commit=handler) as session:
        holder.append(session)
        async with asyncio.timeout(5):
            with pytest.raises(SessionStateError, match="cannot write to or flush its own"):
                await session.run("x")
            with pytest.raises(SessionStateError, match="cannot write to or flush its own"):
                await session.append_message(UserMessage("rejected"))
    assert len(seen) == 1


async def test_commit_deadline_fails_a_hung_handler_and_releases_abort_and_close():
    hung, gate = asyncio.Event(), asyncio.Event()
    delivered: list[SessionCommit] = []

    async def handler(commit: SessionCommit) -> None:
        delivered.append(commit)
        if len(delivered) >= 3:
            hung.set()
            await gate.wait()

    opening = Agent(model=ScriptedModel(["hi"])).session(
        on_commit=handler, commit_timeout_seconds=0.2
    )
    session = await opening.__aenter__()
    try:
        run = asyncio.create_task(session.run("x"))
        async with asyncio.timeout(5):
            await hung.wait()
            with pytest.raises(CommitTimeout, match=r"within 0.2 seconds"):
                await session.abort()
            with pytest.raises(CommitTimeout):
                await run
            with pytest.raises(CommitTimeout):
                await session.append_message(UserMessage("rejected"))
    finally:
        async with asyncio.timeout(5):
            await opening.__aexit__(None, None, None)
    assert not gate.is_set()


async def test_commit_deadline_does_not_fail_a_handler_that_returns_in_time():
    recorder = CommitRecorder()
    agent = Agent(model=ScriptedModel(["hi"]))
    async with agent.session(on_commit=recorder, commit_timeout_seconds=5.0) as session:
        assert (await session.run("x")).text == "hi"
    assert recorder.commits


@pytest.mark.parametrize("seconds", [0, -1.0])
async def test_commit_deadline_must_be_positive(seconds):
    with pytest.raises(ConfigError, match="commit_timeout_seconds"):
        async with Agent(model=ScriptedModel([])).session(
            on_commit=CommitRecorder(), commit_timeout_seconds=seconds
        ):
            pytest.fail("Session opened with an invalid commit deadline")


def agent_session(state: SessionCheckpoint, recorder: CommitRecorder):
    return Agent(model=ScriptedModel([])).session(state=state, on_commit=recorder)


@pytest.mark.parametrize("operation", ["context", "flush"])
async def test_cancelled_delivery_finishes_before_cancellation_is_delivered(operation):
    entered = asyncio.Event()
    release = asyncio.Event()
    commits: list[SessionCommit] = []

    async def slow(commit: SessionCommit) -> None:
        entered.set()
        await release.wait()
        commits.append(commit)

    async with Agent(model=ScriptedModel([])).session(on_commit=slow) as session:
        if operation == "flush":
            await session.emit(MessageAppendedEvent(UserMessage("retained")))
            write = asyncio.create_task(session.flush())
        else:
            write = asyncio.create_task(session.append_message(UserMessage("retained")))
        await asyncio.wait_for(entered.wait(), timeout=5)
        write.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(write, timeout=5)
        assert "retained" in commits[-1].journal[-1].to_json()


async def test_restoring_structured_pending_work_requires_the_registered_output_identity(
    recorder,
):
    class Answer(BaseModel):
        value: int

    contract = OutputContract("answer.v1", Answer)
    model = ScriptedModel([call("final_answer", value=7)])
    agent = Agent(model=model)
    async with agent.session(id="structured", on_commit=recorder) as session:
        await session.pause()
        await session.submit("Answer", output=contract)
    with pytest.raises(ConfigError, match="Restore output contract"):
        async with agent.session(state=recorder.latest("structured")):
            pytest.fail("Missing validators must fail before executing stored work")
    async with agent.session(
        state=recorder.latest("structured"), restore_outputs=[contract]
    ) as restored:
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert len(model.requests) == 1
        assert model.requests[0].output_schema.id == "answer.v1"


async def test_application_ids_need_no_handler_and_invalid_states_are_rejected():
    async with Agent(model=ScriptedModel([])).session(id="conversation-7") as session:
        assert session.id == "conversation-7"
    with pytest.raises(ConfigError, match="SessionCheckpoint"):
        async with Agent(model=ScriptedModel([])).session(state="{}"):  # type: ignore[arg-type]
            pytest.fail("Serialized documents must be validated by the application")


async def test_a_checkpoint_reopens_under_its_own_session_id(recorder):
    agent = Agent(model=ScriptedModel(["first"]))
    async with agent.session(id="conversation-7", on_commit=recorder) as session:
        await session.run("Hello")
    stored = recorder.latest("conversation-7")
    assert stored.session_id == "conversation-7"
    async with agent.session(state=stored, on_commit=recorder) as session:
        assert session.id == "conversation-7"
        await session.flush()
    with pytest.raises(ConfigError, match="session_id"):
        async with agent.session(id="other", state=stored):
            pytest.fail("A checkpoint must not be reopened under another ID")
    async with agent.session(state=stored.state) as branch:
        assert branch.id != "conversation-7"


def test_corrupt_documents_raise_a_format_error():
    with pytest.raises(SessionFormatError):
        SessionCheckpoint.from_dict({"version": 99})
    with pytest.raises(SessionFormatError):
        SessionState.from_dict({"history": [{"position": "first"}]})
    with pytest.raises(SessionFormatError):
        SessionState.from_dict({"plugin_values": {"bad": float("nan")}})


async def test_approval_grants_survive_reopening(recorder):
    class Allow:
        async def ask(self, request: ApprovalRequestedEvent) -> ApprovalResponse:
            return ApprovalResponse(Decision.ALLOW, remember=True)

    agent = Agent(model=ScriptedModel([]))
    async with agent.session(id="grants", on_commit=recorder) as session:
        answer = await session.approvals.request(
            ToolCall("work"), "Authorize work", approver=Allow()
        )
        assert answer is Decision.ALLOW
        grant = session.approvals.grant_for("work", scope="tool-policy")
        assert grant is not None
        request_id = grant.request_id
    async with agent.session(
        id="grants", state=recorder.latest("grants"), on_commit=recorder
    ) as restored:
        grant = restored.approvals.grant_for("work", scope="tool-policy")
        assert grant is not None and grant.request_id == request_id
        await restored.approvals.revoke(request_id)
    async with agent.session(state=recorder.latest("grants")) as restored:
        assert not restored.approvals.grants


async def test_images_and_spilled_text_survive_a_paused_turn(recorder):
    entered, release = asyncio.Event(), asyncio.Event()
    text = "large output " * 2000

    class Source(Plugin):
        @tool(readonly=True)
        async def source(self) -> str:
            entered.set()
            await release.wait()
            return text

    model = ScriptedModel([call(Source.source), "finished"])
    agent = Agent(model=model, plugins=[Source(), Spill(threshold_tokens=32)])
    async with agent.session(id="resources", on_commit=recorder) as session:
        image = await session.assets.put(b"pixels\xff\x00", media_type="image/png")
        await session.submit(UserMessage((ImageReference(image),)))
        await asyncio.wait_for(entered.wait(), timeout=5)
        pausing = asyncio.create_task(session.pause())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(pausing, timeout=5)
    async with agent.session(state=recorder.latest("resources")) as restored:
        assert await restored.assets.read(image) == b"pixels\xff\x00"
        resource = restored.log.resources[0]
        assert await restored.scope[SpillStore].read(resource.path, limit=16000) == text[:16000]
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert restored.messages[-1].text == "finished"


@pytest.mark.parametrize("boundary", ["flush", "close"])
async def test_intermediate_changes_wait_for_the_requested_boundary(boundary):
    recorder = CommitRecorder()
    agent = Agent(model=ScriptedModel([]))
    async with agent.session(id="batch", on_commit=recorder) as session:
        await session.scope[PluginValues].commit("seed", "ready")
        before = len(recorder.commits)
        await session.emit(MessageAppendedEvent(UserMessage("first")))
        await session.emit(MessageAppendedEvent(UserMessage("second")))
        assert len(recorder.commits) == before
        if boundary == "flush":
            await session.flush()
            assert len(recorder.commits) == before + 1
    assert len(recorder.commits) == before + 1
    assert len(recorder.commits[-1].journal) == 2
    async with agent.session(state=recorder.latest("batch")) as restored:
        assert [message.text for message in restored.messages][-2:] == ["first", "second"]


async def test_undelivered_changes_are_reported_when_closing():
    recorder = CommitRecorder()

    def unavailable(_commit: SessionCommit) -> None:
        raise OSError("idle storage unavailable")

    recorder.before = unavailable
    agent = Agent(model=ScriptedModel([]))
    with pytest.raises(OSError, match="idle storage unavailable"):
        async with agent.session(on_commit=recorder) as session:
            await session.emit(MessageAppendedEvent(UserMessage("pending fact")))


async def test_model_request_is_not_executed_when_its_intent_cannot_be_saved():
    recorder = CommitRecorder()
    model = ScriptedModel(["must not execute"])

    def unavailable(commit: SessionCommit) -> None:
        if any(isinstance(entry.record, CheckpointRequest) for entry in commit.journal):
            raise OSError("model intent unavailable")

    recorder.before = unavailable
    async with Agent(model=model).session(on_commit=recorder) as session:
        with pytest.raises(OSError, match="model intent unavailable"):
            await session.run("Work")
    assert not model.requests
