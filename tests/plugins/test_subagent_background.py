import asyncio
import json

import pytest

from harnessed import (
    Agent,
    Plugin,
    PluginOrder,
    SessionState,
    SystemPrompt,
    ToolCall,
    tool,
)
from harnessed.core.messages import ToolMessage, ToolStatus
from harnessed.events import SubagentStartedEvent, SubagentStatus, SubagentUpdatedEvent
from harnessed.hooks import SessionAppended
from harnessed.integrations.json import JsonSessionStore
from harnessed.models import ModelChunk
from harnessed.plugins import Approvals, Subagents
from harnessed.plugins.subagents import ChildActivity, ChildReportState
from harnessed.testing import CallbackModel, ScriptedModel


def _spawn(task="Review the code", *, id=None):
    return ToolCall("spawn", {"agent": "review", "task": task}, **({"id": id} if id else {}))


def _parent(child, model, **limits):
    return Agent(
        model=model,
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
        plugins=[
            Subagents({"review": child}, **limits),
            Approvals(allow=[Subagents.spawn, Subagents.send, Subagents.cancel]),
        ],
    )


async def test_spawn_returns_before_child_and_completion_wakes_idle_coordinator(tmp_path):
    started, release, notified = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def child_response(request):
        started.set()
        await asyncio.wait_for(release.wait(), 3)
        yield ModelChunk(text="Child result")

    calls = 0

    async def parent_response(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield ModelChunk(tool_call=_spawn())
        elif calls == 2:
            yield ModelChunk(text="Coordinator worked independently")
        else:
            notification = json.loads(request.messages[-1].text)
            assert notification["type"] == "subagent.finished"
            assert notification["data"]["text"] == "Child result"
            notified.set()
            yield ModelChunk(text="Handled result")

    store = JsonSessionStore(root=tmp_path)
    parent = _parent(Agent(model=CallbackModel(child_response)), CallbackModel(parent_response))
    parent.store = store
    async with parent.session(id="parent") as session:
        first = await asyncio.wait_for(session.run("Start"), 2)
        await asyncio.wait_for(started.wait(), 2)
        assert first.text == "Coordinator worked independently"
        assert session.children[0].status is SubagentStatus.RUNNING
        state = SessionState.from_json(session.snapshot().to_json())
        assert state.children == session.children
        await session.flush()
        stored = await store.load_session("parent")
        assert stored.checkpoint.state.children[0].status is SubagentStatus.RUNNING
        release.set()
        await asyncio.wait_for(notified.wait(), 2)
        await asyncio.wait_for(session.wait_until_idle(), 2)
        assert session.children[0].status is SubagentStatus.COMPLETED
        assert session.children[0].runtime.steps_completed == 1
    async with Agent(model=ScriptedModel([]), store=store).session(id="parent") as restored:
        assert restored.children[0].text == "Child result"
    async with Agent(model=ScriptedModel([])).session(state=state) as copied:
        assert copied.children[0].status is SubagentStatus.INTERRUPTED


@pytest.mark.parametrize(
    "timeout,save_delay",
    [
        pytest.param(0.0, 0.0, id="poll"),
        pytest.param(5.0, 0.0, id="wait"),
        pytest.param(5.0, 0.2, id="slow-checkpoints"),
    ],
)
async def test_status_asks_child_coalesces_requests_and_does_not_finish_task(
    timeout, save_delay, tmp_path, monkeypatch
):
    # The status timeout bounds the reply wait, not the surrounding durable writes.
    # Keep a separate local coordination deadline with room for slow Windows fsyncs.
    coordination_timeout = 15
    blocked, release, continue_work, report_notified = [asyncio.Event() for _ in range(4)]
    child_requests = []
    child_id = None
    status_requested = asyncio.Event()

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self):
            blocked.set()
            await asyncio.wait_for(release.wait(), coordination_timeout)
            return "First part complete"

    async def child_response(request):
        child_requests.append(request)
        if len(child_requests) == 1:
            yield ModelChunk(tool_call=ToolCall("work"))
        elif len(child_requests) == 2:
            inquiries = [
                json.loads(message.text)
                for message in request.messages
                if message.text.startswith('{"type": "subagent.status.request"')
            ]
            assert len(inquiries) == 1
            assert any(message.text == "Focus on refresh races" for message in request.messages)
            yield ModelChunk(
                tool_call=ToolCall(
                    "answer_status",
                    {"request_id": "another-child-request", "message": "Invalid"},
                )
            )
            yield ModelChunk(
                tool_call=ToolCall(
                    "answer_status",
                    {
                        "request_id": inquiries[0]["request_id"],
                        "message": "Login checked; inspecting refresh races.",
                    },
                )
            )
        else:
            results = [message for message in request.messages if isinstance(message, ToolMessage)]
            assert [message.status for message in results[-2:]] == [ToolStatus.ERROR, ToolStatus.OK]
            await asyncio.wait_for(continue_work.wait(), coordination_timeout)
            yield ModelChunk(text="Final task result")

    async def parent_response(request):
        nonlocal child_id
        last = next(
            message
            for message in reversed(request.messages)
            if not message.id.startswith("mount-context:")
        )
        if last.text == "Start":
            yield ModelChunk(tool_call=_spawn())
        elif isinstance(last, ToolMessage):
            payload = json.loads(last.text)
            if child_id is None:
                child_id = payload["child_id"]
            yield ModelChunk(text="Coordinator continues")
        elif last.text == "Ask":
            yield ModelChunk(
                tool_call=ToolCall(
                    "send",
                    {
                        "child_id": child_id,
                        "message": "Focus on refresh races",
                    },
                )
            )
            yield ModelChunk(
                tool_call=ToolCall(
                    "status",
                    {
                        "child_id": child_id,
                        "timeout": timeout,
                    },
                )
            )
            if timeout == 0:
                yield ModelChunk(
                    tool_call=ToolCall(
                        "status",
                        {
                            "child_id": child_id,
                            "timeout": 0,
                        },
                    )
                )
        else:
            notification = json.loads(last.text)
            if notification["type"] == "subagent.status":
                assert notification["data"]["text"] == "Login checked; inspecting refresh races."
                report_notified.set()
            yield ModelChunk(text="Notification handled")

    def requested(event: SessionAppended):
        if isinstance(event.event, SubagentUpdatedEvent):
            report = event.event.runtime.report
            if report is not None and report.state is ChildReportState.PENDING:
                status_requested.set()
                if timeout:
                    release.set()

    parent = _parent(
        Agent(model=CallbackModel(child_response), plugins=[Work()]),
        CallbackModel(parent_response),
    )
    store = JsonSessionStore(root=tmp_path)
    if save_delay:
        save = store.save_session

        async def delayed_save(conversation_id, commit, *, expected_revision):
            await asyncio.sleep(save_delay)
            return await save(conversation_id, commit, expected_revision=expected_revision)

        monkeypatch.setattr(store, "save_session", delayed_save)
    parent.store = store
    async with parent.session() as session:
        session.on(SessionAppended, requested)
        await session.run("Start")
        await asyncio.wait_for(blocked.wait(), coordination_timeout)
        result = await asyncio.wait_for(session.run("Ask"), coordination_timeout)
        reports = [
            json.loads(value.text)["report"]
            for call, value in result.tool_calls
            if call.name == "status"
        ]
        assert status_requested.is_set()
        if timeout == 0:
            assert len(reports) == 2
            assert {report["state"] for report in reports} == {"pending"}
            assert len({report["request_id"] for report in reports}) == 1
            assert session.children[0].runtime.activity is ChildActivity.TOOL
            assert len(child_requests) == 1
            release.set()
            await asyncio.wait_for(report_notified.wait(), coordination_timeout)
            await asyncio.wait_for(session.wait_until_idle(), coordination_timeout)
        else:
            assert reports[0]["state"] == "answered"
            assert not report_notified.is_set()
        assert session.children[0].status is SubagentStatus.RUNNING
        assert session.children[0].runtime.report.text == "Login checked; inspecting refresh races."
        await session.flush()
        stored = await parent.store.load_session(session.id)
        assert (
            stored.checkpoint.state.children[0].runtime.report == session.children[0].runtime.report
        )
        continue_work.set()
        # Observe completion without starting another parent turn ourselves.
        async with asyncio.timeout(coordination_timeout):
            async for _event in session.subscribe():
                if session.children[0].status is SubagentStatus.COMPLETED:
                    break
        assert session.children[0].text == "Final task result"
        assert len(child_requests) == 3


@pytest.mark.parametrize("action", ["abort", "close", "unmount", "cancel"])
async def test_background_children_are_drained_even_when_parent_is_idle(action):
    started, cleaned = asyncio.Event(), asyncio.Event()
    child_id = None

    async def child_response(request):
        started.set()
        try:
            await asyncio.wait_for(asyncio.Event().wait(), 3)
            yield ModelChunk(text="unreachable")
        finally:
            cleaned.set()

    async def parent_response(request):
        nonlocal child_id
        last = next(
            message
            for message in reversed(request.messages)
            if not message.id.startswith("mount-context:")
        )
        if last.text == "Start":
            yield ModelChunk(tool_call=_spawn())
        elif last.text == "Cancel":
            yield ModelChunk(tool_call=ToolCall("cancel", {"child_id": child_id}))
        else:
            if isinstance(last, ToolMessage) and child_id is None:
                child_id = json.loads(last.text)["child_id"]
            yield ModelChunk(text="Done")

    parent = Agent(
        model=CallbackModel(parent_response),
        plugins=[Approvals.allow_all()],
    )
    async with parent.session() as session:
        mount = await session.use(
            Subagents({"review": Agent(model=CallbackModel(child_response))}),
            section_delivery="context",
        )
        await session.run("Start")
        await asyncio.wait_for(started.wait(), 2)
        if action == "abort":
            await asyncio.wait_for(session.abort(), 2)
        elif action == "close":
            await asyncio.wait_for(session.aclose(), 2)
        elif action == "unmount":
            await asyncio.wait_for(mount.aclose(), 2)
        else:
            result = await asyncio.wait_for(session.run("Cancel"), 2)
            assert json.loads(result.tool_calls[0][1].text)["status"] == "cancelled"
        assert cleaned.is_set()
        assert session.children[0].status is SubagentStatus.CANCELLED


async def test_wait_timeout_keeps_child_running_and_spawn_limits_include_queued_children():
    started = asyncio.Event()

    async def child_response(request):
        started.set()
        await asyncio.wait_for(asyncio.Event().wait(), 3)
        yield ModelChunk(text="unreachable")

    child_ids = []
    calls = 0

    async def parent_response(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            for _ in range(3):
                yield ModelChunk(tool_call=_spawn())
        elif calls == 2:
            results = [message for message in request.messages if isinstance(message, ToolMessage)]
            child_ids.extend(json.loads(message.text)["child_id"] for message in results[:2])
            assert results[2].status is ToolStatus.ERROR
            yield ModelChunk(tool_call=ToolCall("wait", {"child_id": child_ids[0], "timeout": 0}))
        else:
            assert json.loads(request.messages[-1].text)["status"] == "running"
            yield ModelChunk(text="Continue independently")

    parent = _parent(
        Agent(model=CallbackModel(child_response)),
        CallbackModel(parent_response),
        max_parallel=1,
        max_calls=2,
    )
    async with parent.session() as session:
        result = await session.run("Start")
        await asyncio.wait_for(started.wait(), 2)
        assert result.text == "Continue independently"
        assert len(session.children) == 2
        assert session.children[1].runtime.activity is ChildActivity.QUEUED
        assert all(child.status is SubagentStatus.RUNNING for child in session.children)


async def test_tools_are_only_registered_by_the_optional_plugin():
    model = ScriptedModel(["Plain agent"])
    await Agent(model=model).run("Hello")
    forbidden = {"spawn", "status", "send", "wait", "cancel", "delegate", "answer_status"}
    assert forbidden.isdisjoint(tool.name for tool in model.requests[0].tools)


async def test_abort_during_start_admission_records_a_terminal_child():
    admitting = asyncio.Event()

    async def hold_start(event: SubagentStartedEvent):
        admitting.set()
        await asyncio.wait_for(asyncio.Event().wait(), 3)

    child_model = ScriptedModel(["Must not start"])
    parent = _parent(
        Agent(model=child_model),
        ScriptedModel([[ModelChunk(tool_call=_spawn())], "unused"]),
    )
    async with parent.session() as session:
        session.on(SubagentStartedEvent, hold_start)
        handle = await session.submit("Start")
        await asyncio.wait_for(admitting.wait(), 2)
        await asyncio.wait_for(session.abort(), 2)
        await asyncio.wait_for(handle.result(), 2)
        assert session.children[0].status is SubagentStatus.CANCELLED
        assert child_model.requests == []


async def test_status_while_awaiting_child_approval_returns_facts_then_terminal_unavailable():
    approval_requested, approve, completed = [asyncio.Event() for _ in range(3)]
    effects = []
    child_id = None

    class Write(Plugin):
        @tool
        async def write(self):
            effects.append("written")
            return "Written"

    async def approver(request):
        assert "Subagent review" in request.reason
        approval_requested.set()
        await asyncio.wait_for(approve.wait(), 3)
        return True

    async def parent_response(request):
        nonlocal child_id
        last = request.messages[-1]
        if last.text == "Start":
            yield ModelChunk(tool_call=_spawn())
        elif last.text == "Ask":
            yield ModelChunk(tool_call=ToolCall("status", {"child_id": child_id, "timeout": 0}))
        else:
            if isinstance(last, ToolMessage) and child_id is None:
                child_id = json.loads(last.text)["child_id"]
            elif last.text.startswith('{"type": "subagent.finished"'):
                completed.set()
            yield ModelChunk(text="Continue")

    parent = Agent(
        model=CallbackModel(parent_response),
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
        plugins=[
            Subagents(
                {
                    "review": Agent(
                        model=ScriptedModel(
                            [[ModelChunk(tool_call=ToolCall("write"))], "Child done"]
                        ),
                        plugins=[Write()],
                    )
                }
            ),
            Approvals(allow=[Subagents.spawn], approver=approver),
        ],
    )
    async with parent.session() as session:
        await session.run("Start")
        await asyncio.wait_for(approval_requested.wait(), 2)
        result = await session.run("Ask")
        status = json.loads(result.tool_calls[0][1].text)
        assert status["activity"] == "waiting_approval"
        assert status["report"]["state"] == "pending"
        assert effects == []
        approve.set()
        await asyncio.wait_for(completed.wait(), 2)
        assert effects == ["written"]
        assert session.children[0].runtime.report.state is ChildReportState.UNAVAILABLE


async def test_queued_child_accepts_guidance_and_status_without_starting_an_extra_turn():
    first_started, release_first, second_completed = [asyncio.Event() for _ in range(3)]
    child_requests = []
    ids = []
    parent_calls = 0

    async def child_response(request):
        child_requests.append(request)
        if request.messages[-1].text == "First":
            first_started.set()
            await asyncio.wait_for(release_first.wait(), 3)
        yield ModelChunk(text="Task done")

    async def parent_response(request):
        nonlocal parent_calls
        parent_calls += 1
        if parent_calls == 1:
            yield ModelChunk(tool_call=_spawn("First"))
            yield ModelChunk(tool_call=_spawn("Second"))
        elif parent_calls == 2:
            ids.extend(
                json.loads(message.text)["child_id"]
                for message in request.messages
                if isinstance(message, ToolMessage)
            )
            yield ModelChunk(
                tool_call=ToolCall("send", {"child_id": ids[1], "message": "Guidance"})
            )
            yield ModelChunk(tool_call=ToolCall("status", {"child_id": ids[1], "timeout": 0}))
        else:
            if request.messages[-1].text.startswith('{"type": "subagent.finished"'):
                message = json.loads(request.messages[-1].text)
                if message["child_id"] == ids[1]:
                    second_completed.set()
            yield ModelChunk(text="Parent done")

    parent = _parent(
        Agent(model=CallbackModel(child_response)),
        CallbackModel(parent_response),
        max_parallel=1,
    )
    async with parent.session() as session:
        await session.run("Start")
        await asyncio.wait_for(first_started.wait(), 2)
        assert len(child_requests) == 1
        assert session.children[1].runtime.report.state is ChildReportState.PENDING
        release_first.set()
        await asyncio.wait_for(second_completed.wait(), 2)
        second_messages = child_requests[1].messages
        assert any(message.text == "Second" for message in second_messages)
        assert any(message.text == "Guidance" for message in second_messages)
        assert any(
            '"type": "subagent.status.request"' in message.text for message in second_messages
        )
        assert session.children[1].status is SubagentStatus.COMPLETED


@pytest.mark.parametrize("ending", ["timeout", "parent_error"])
async def test_background_timeout_or_parent_failure_drains_children(ending):
    started, cleaned, notified = [asyncio.Event() for _ in range(3)]
    parent_calls = 0

    async def child_response(request):
        started.set()
        try:
            await asyncio.wait_for(asyncio.Event().wait(), 3)
            yield ModelChunk(text="unreachable")
        finally:
            cleaned.set()

    async def parent_response(request):
        nonlocal parent_calls
        parent_calls += 1
        if parent_calls == 1:
            yield ModelChunk(tool_call=_spawn())
        elif parent_calls == 2:
            await asyncio.wait_for(started.wait(), 2)
            if ending == "parent_error":
                raise RuntimeError("Coordinator failed")
            yield ModelChunk(text="Continue")
        else:
            notified.set()
            yield ModelChunk(text="Child timed out")

    parent = _parent(
        Agent(model=CallbackModel(child_response)),
        CallbackModel(parent_response),
        timeout=0.1 if ending == "timeout" else 5,
    )
    async with parent.session() as session:
        if ending == "parent_error":
            with pytest.raises(RuntimeError, match="Coordinator failed"):
                await asyncio.wait_for(session.run("Start"), 2)
            assert session.children[0].status is SubagentStatus.CANCELLED
        else:
            await session.run("Start")
            await asyncio.wait_for(notified.wait(), 2)
            assert session.children[0].status is SubagentStatus.TIMED_OUT
        assert cleaned.is_set()
