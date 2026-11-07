import asyncio
import json
from dataclasses import replace

import pytest

from harnessed import (
    Agent,
    ApprovalAnswer,
    Plugin,
    PolicyDecision,
    SessionState,
    ToolCall,
    ToolContext,
    tool,
)
from harnessed.core.events.subagents import SubagentLinkedEvent
from harnessed.core.messages import ToolStatus
from harnessed.core.model import ModelChunk, ModelUsage
from harnessed.core.session.codec import decode_event, encode_event
from harnessed.core.session.session import SessionLog
from harnessed.errors import SessionWriteConflict
from harnessed.events import (
    ApprovalNeededEvent,
    ApprovalRequestedEvent,
    ApprovalStatus,
    SubagentApprovalRequestedEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
    SubagentStatus,
    ToolProgressEvent,
)
from harnessed.integrations.json import JsonSessionStore
from harnessed.kernel import Inject
from harnessed.models import UserMessage
from harnessed.plugins import AgentLoop, Approvals, Subagents
from harnessed.sessions import SessionCommit, StoredSession
from harnessed.testing import CallbackModel, ScriptedModel
from tests._audit import Audit, AuditProjection
from tests._checkpoints import checkpoint


class RecordingStore:
    def __init__(self) -> None:
        self.rows: dict[str, StoredSession] = {}
        self.commits: list[SessionCommit] = []

    async def load_session(self, conversation_id: str) -> StoredSession | None:
        return self.rows.get(conversation_id)

    async def save_session(
        self, conversation_id: str, commit: SessionCommit, *, expected_revision: int | None
    ) -> int:
        stored = self.rows.get(conversation_id)
        revision = stored.revision if stored is not None else None
        if revision != expected_revision:
            raise SessionWriteConflict(conversation_id)
        revision = 1 if revision is None else revision + 1
        self.rows[conversation_id] = StoredSession(commit.checkpoint, revision)
        self.commits.append(commit)
        return revision


def delegate(agent="review", task="Review this task", *, id=None):
    options = {} if id is None else {"id": id}
    return ToolCall("delegate", {"agent": agent, "task": task}, **options)


def coordinator(child, calls=None, **limits):
    model = ScriptedModel(
        [[ModelChunk(tool_call=call) for call in (calls or [delegate()])], "Parent answer"]
    )
    return Agent(
        model=model,
        instructions="Private parent instructions",
        plugins=[Subagents({"review": child}, **limits), Approvals(default=PolicyDecision.ALLOW)],
    )


class Progress(Plugin):
    @tool(readonly=True)
    async def inspect(self, *, context: Inject[ToolContext]) -> str:
        await context.progress("Inspecting")
        return "Inspected"


async def test_isolated_sessions_progress_receipts_and_usage():
    child_model = ScriptedModel(
        [
            [ModelChunk(tool_call=ToolCall("inspect")), ModelChunk(usage=ModelUsage(3, 2))],
            [ModelChunk(text="Child answer", usage=ModelUsage(4, 1))],
        ]
    )
    store = RecordingStore()
    child = Agent(
        model=child_model, instructions="Specialist instructions", plugins=[Progress()], store=store
    )
    parent = coordinator(child)
    async with parent.session() as session:
        await session.append_message(UserMessage("Private additive parent context"))
        result = await session.run("Secret parent prompt")
        parent_id = session.id
        snapshot = session.children[0]
        assert snapshot.status is SubagentStatus.COMPLETED
        assert snapshot.text == "Child answer"
        assert snapshot.usage.total_tokens == 10
        assert session.usage.total_tokens == 0
        assert session.tree_usage.total_tokens == 10
        assert session.parent is None
        assert result.text == "Parent answer"
        receipt = json.loads(result.tool_calls[0][1].text)
        assert receipt["child_session_id"] == snapshot.id
        assert receipt["status"] == "completed"
        assert any(
            isinstance(event, ToolProgressEvent) and "Inspecting" in event.text
            for event in result.events
        )
        for event in result.events:
            if isinstance(event, SubagentStartedEvent | SubagentFinishedEvent):
                assert decode_event(encode_event(event)) == event
        assert snapshot.id in store.rows
    async with child.session(id=snapshot.id) as restored:
        assert restored.parent.parent_session_id == parent_id
        assert restored.parent.parent_call_id == snapshot.call_id
        assert restored.parent.depth == 1
        assert restored.messages[-1].text == "Child answer"
    rendered = str(child_model.requests[0].messages)
    assert "Specialist instructions" in rendered
    assert "Review this task" in rendered
    assert "Secret parent prompt" not in rendered
    assert "Private parent instructions" not in rendered
    assert "Private additive parent context" not in rendered
    assert {tool.name for tool in child_model.requests[0].tools} == {"inspect", "answer_status"}


async def test_parent_stream_exposes_child_lifecycle_and_routes_approvals():
    effects = []
    child_callbacks = []

    class Write(Plugin):
        @tool
        async def write(self) -> str:
            effects.append("written")
            return "Written"

    child = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("write"))], "Done"]),
        plugins=[
            Write(),
            Approvals(approver=lambda request: child_callbacks.append(request) or True),
        ],
    )
    parent = coordinator(child)
    events = []
    async with parent.session() as session:
        async with session.stream("Delegate") as run_stream:
            async for event in run_stream:
                events.append(event)
                if isinstance(event, ApprovalNeededEvent):
                    assert "Subagent review" in event.reason
                    assert event.call.name == "write"
                    await session.approvals.answer(event.request_id, ApprovalAnswer.ALLOW)
        assert effects == ["written"]
        assert child_callbacks == []
        requests = [event for event in events if isinstance(event, ApprovalNeededEvent)]
        assert len(requests) == 1
        result = events[-1].result
        recorded = [event for event in result.events if isinstance(event, ApprovalRequestedEvent)]
        assert len(recorded) == 1
        assert recorded[0].scope.startswith("subagent:")
    assert any(isinstance(event, SubagentStartedEvent) for event in events)
    assert any(isinstance(event, SubagentFinishedEvent) for event in events)
    assert any(isinstance(event, SubagentApprovalRequestedEvent) for event in events)


@pytest.mark.parametrize(
    "child_timeout,parent_timeout,status",
    [(0.03, 1.0, ApprovalStatus.INTERRUPTED), (1.0, 0.03, ApprovalStatus.UNAVAILABLE)],
)
async def test_forwarded_approvals_obey_both_child_and_parent_response_deadlines(
    child_timeout, parent_timeout, status
):
    effects = []
    stopped = asyncio.Event()

    class Write(Plugin):
        @tool
        async def write(self) -> str:
            effects.append("written")
            return "written"

    async def respond(request):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    child = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("write"))], "done"]),
        plugins=[Write(), Approvals(timeout=child_timeout)],
    )
    parent = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=delegate())], "done"]),
        plugins=[
            Subagents({"review": child}),
            Approvals(default=PolicyDecision.ALLOW, approver=respond, timeout=parent_timeout),
        ],
    )
    async with parent.session() as session:
        audit = Audit(session)
        async with asyncio.timeout(3):
            await session.run("delegate")
        assert effects == [] and stopped.is_set()
        records = AuditProjection(audit.events).approvals.records
        assert len(records) == 1 and records[0].resolution.status is status
        assert not session.approvals.pending


async def test_unanswered_child_approval_does_not_execute_tool():
    effects = []

    class Write(Plugin):
        @tool
        async def write(self) -> str:
            effects.append("written")
            return "Written"

    child = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("write"))], "Denied"]),
        plugins=[Write()],
    )
    parent = coordinator(child)
    await parent.run("Delegate")
    assert effects == []


@pytest.mark.parametrize("mode", ["timeout", "abort", "close"])
async def test_cleanup_cancels_child_driver_and_drains_effects(mode):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    cleaned = asyncio.Event()

    async def blocked(request):
        started.set()
        try:
            await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
            yield ModelChunk(text="unreachable")
        finally:
            cancelled.set()

    class Resource(Plugin):
        async def setup(self, scope):
            scope.effect(cleaned.set)

    child = Agent(model=CallbackModel(blocked), plugins=[Resource()])
    parent = coordinator(child, timeout=1.0 if mode == "timeout" else 5.0)
    async with parent.session() as session:
        handle = await session.submit("Delegate")
        await asyncio.wait_for(started.wait(), 2)
        if mode == "abort":
            await session.abort()
        elif mode == "close":
            await session.aclose()
        else:
            await asyncio.wait_for(handle.result(), 3)
        assert cancelled.is_set()
        assert cleaned.is_set()
        assert session.children[0].status is (
            SubagentStatus.TIMED_OUT if mode == "timeout" else SubagentStatus.CANCELLED
        )
        assert session.children[0].text


async def test_dynamic_unmount_drains_active_child():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked(request):
        started.set()
        try:
            await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
            yield ModelChunk()
        finally:
            cancelled.set()

    child = Agent(model=CallbackModel(blocked))
    parent = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=delegate())], "Done"]),
        plugins=[Approvals(default=PolicyDecision.ALLOW)],
    )
    async with parent.session() as session:
        mount = await session.mount(Subagents({"review": child}), section_delivery="context")
        handle = await session.submit("Delegate")
        await asyncio.wait_for(started.wait(), 2)
        await mount.aclose()
        assert cancelled.is_set()
        assert session.children[0].status is SubagentStatus.CANCELLED
        result = await asyncio.wait_for(handle.result(), 2)
        assert result.tool_calls[0][1].status is ToolStatus.CANCELLED


async def test_parallel_limit_and_session_call_limit():
    active = 0
    maximum = 0

    async def respond(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.01)
            yield ModelChunk(text="Child answer")
        finally:
            active -= 1

    child = Agent(model=CallbackModel(respond))
    parent = coordinator(child, [delegate() for _ in range(5)], max_parallel=2, max_calls=3)
    async with parent.session() as session:
        result = await session.run("Delegate")
        assert maximum == 2
        assert active == 0
        assert len(session.children) == 3
        assert sum(value.status is ToolStatus.ERROR for _, value in result.tool_calls) == 2


@pytest.mark.parametrize("nested_tool", ["delegate", "spawn"])
async def test_default_depth_limit_is_inherited_and_cannot_be_raised_by_child(nested_tool):
    leaf_model = ScriptedModel(["Leaf"])
    leaf = Agent(model=leaf_model)
    store = RecordingStore()
    middle = Agent(
        model=ScriptedModel(
            [
                [ModelChunk(tool_call=ToolCall(nested_tool, {"agent": "leaf", "task": "Task"}))],
                "Middle",
            ]
        ),
        store=store,
        plugins=[Subagents({"leaf": leaf}, max_depth=10), Approvals(default=PolicyDecision.ALLOW)],
    )
    parent = coordinator(middle)
    await parent.run("Delegate")
    assert leaf_model.requests == []
    child_id = next(iter(store.rows))
    async with middle.session(id=child_id) as session:
        assert session.children == ()


async def test_nested_usage_counts_each_model_once():
    leaf = Agent(model=ScriptedModel([[ModelChunk(text="Leaf", usage=ModelUsage(3, 2))]]))
    middle = Agent(
        model=ScriptedModel(
            [
                [ModelChunk(tool_call=delegate("leaf"))],
                [ModelChunk(text="Middle", usage=ModelUsage(2))],
            ]
        ),
        plugins=[Subagents({"leaf": leaf}, max_depth=2), Approvals(default=PolicyDecision.ALLOW)],
    )
    parent = coordinator(middle, max_depth=2)
    async with parent.session() as session:
        await session.run("Delegate")
        assert session.tree_usage.total_tokens == 7
        assert session.usage.total_tokens == 0


@pytest.mark.parametrize("limit", [{"max_steps": 1}, {"token_budget": 1}])
async def test_child_turn_limits_are_reported(limit):
    child = Agent(
        model=ScriptedModel(
            [[ModelChunk(tool_call=ToolCall("inspect")), ModelChunk(usage=ModelUsage(5))], "Unused"]
        ),
        plugins=[Progress()],
    )
    parent = coordinator(child, **limit)
    async with parent.session() as session:
        await session.run("Delegate")
        assert session.children[0].status is SubagentStatus.LIMITED
        assert session.tree_usage.total_tokens == 5


async def test_persistent_parent_and_child_are_replayable_without_running_models(tmp_path):
    store = JsonSessionStore(root=tmp_path)
    child = Agent(model=ScriptedModel(["Child answer"]), store=store)
    parent = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=delegate())], "Parent answer"]),
        store=store,
        plugins=[Subagents({"review": child}), Approvals(default=PolicyDecision.ALLOW)],
    )
    async with parent.session(id="parent") as session:
        result = await session.run("Delegate")
        snapshots = session.children
        assert result.state.children == snapshots
    restored = Agent(model=ScriptedModel([]), store=store)
    async with restored.session(id="parent") as session:
        assert session.children == snapshots
    async with restored.session(id=snapshots[0].id) as session:
        assert session.parent.parent_session_id == "parent"
        assert session.messages[-1].text == "Child answer"


async def test_interrupted_parent_marks_child_without_repeating_work():
    from harnessed.events import TurnStartedEvent

    store = RecordingStore()
    started = SubagentStartedEvent("child-one", "review", "task", "turn", "call", 1)
    suffix = (TurnStartedEvent("turn", "input"), started)
    seed = replace(checkpoint(), events=suffix)
    store.rows["parent"] = StoredSession(seed, 1)
    agent = Agent(model=ScriptedModel([]), store=store)
    async with agent.session(id="parent") as session:
        assert session.children[0].status is SubagentStatus.INTERRUPTED
    async with agent.session(id="parent") as session:
        assert session.children[0].status is SubagentStatus.INTERRUPTED
    receipts = [
        change.event
        for commit in store.commits
        for change in commit.changes
        if isinstance(change.event, SubagentFinishedEvent)
    ]
    assert len(receipts) == 1


@pytest.mark.parametrize(
    "arguments", [{"agent": "unknown", "task": "x"}, {"agent": "review", "task": " "}]
)
async def test_invalid_delegations_start_no_children(arguments):
    child = Agent(model=ScriptedModel([]))
    parent = coordinator(child, [ToolCall("delegate", arguments)])
    async with parent.session() as session:
        result = await session.run("Delegate")
        assert session.children == ()
        assert result.tool_calls[0][1].status is ToolStatus.ERROR


async def test_delegate_overrides_ordinary_tool_timeout():
    async def respond(request):
        await asyncio.sleep(0.03)
        yield ModelChunk(text="Child answer")

    parent = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=delegate())], "Parent answer"]),
        plugins=[
            AgentLoop(tool_timeout=0.001),
            Subagents({"review": Agent(model=CallbackModel(respond))}),
            Approvals(default=PolicyDecision.ALLOW),
        ],
    )
    async with parent.session() as session:
        await session.run("Delegate")
        assert session.children[0].status is SubagentStatus.COMPLETED


async def test_configuration_and_link_validation():
    for values in ({"max_parallel": 0}, {"max_calls": 0}, {"max_depth": 0}, {"timeout": 0.0}):
        with pytest.raises(ValueError):
            Subagents({"review": Agent()}, **values)
    with pytest.raises(ValueError):
        Subagents({})
    with pytest.raises(TypeError):
        Subagents({"bad": object()})
    link = SubagentLinkedEvent("parent", "turn", "call", "review", 1, 1)
    assert decode_event(encode_event(link)) == link
    with pytest.raises(ValueError):
        from harnessed.core.session.session import SessionLog

        await SessionLog("child").append(replace(link, depth=2))


async def test_child_failure_is_reported_without_cancelling_sibling():
    async def respond(request):
        if request.messages[-1].text == "fail":
            raise RuntimeError("private provider detail")
        await asyncio.sleep(0)
        yield ModelChunk(text="Success")

    parent = coordinator(
        Agent(model=CallbackModel(respond)), [delegate(task="fail"), delegate(task="succeed")]
    )
    async with parent.session() as session:
        result = await session.run("Delegate")
        assert [item.status for item in session.children] == [
            SubagentStatus.FAILED,
            SubagentStatus.COMPLETED,
        ]
        assert "private provider detail" not in result.tool_calls[0][1].text
        assert result.tool_calls[1][1].status is ToolStatus.OK


@pytest.mark.parametrize("mode", ["pause", "stop"])
async def test_graceful_parent_controls_wait_for_child(mode):
    started = asyncio.Event()
    release = asyncio.Event()

    async def respond(request):
        started.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        yield ModelChunk(text="Done")

    parent = coordinator(Agent(model=CallbackModel(respond)))
    async with parent.session() as session:
        handle = await session.submit("Delegate")
        await asyncio.wait_for(started.wait(), 2)
        control = asyncio.create_task(session.pause() if mode == "pause" else session.stop())
        await asyncio.sleep(0.01)
        assert not control.done()
        release.set()
        await asyncio.wait_for(control, 2)
        assert session.children[0].status is SubagentStatus.COMPLETED
        if mode == "pause":
            await session.resume()
        await asyncio.wait_for(handle.result(), 2)


async def test_always_grants_are_scoped_to_child_and_parent_revocation_is_effective():
    effects = []
    parent_session = []

    class Write(Plugin):
        @tool
        async def write(self) -> str:
            effects.append("written")
            if len(effects) == 2:
                session = parent_session[0]
                await session.approvals.revoke(session.approvals.grants[0].request_id)
            return "Written"

    store = RecordingStore()
    child = Agent(
        store=store,
        model=ScriptedModel(
            [*[[ModelChunk(tool_call=ToolCall("write"))] for _ in range(3)], "Done"]
        ),
        plugins=[Write()],
    )
    parent = coordinator(child)
    requests = []
    async with parent.session() as session:
        parent_session.append(session)
        async with session.stream("Delegate") as run_stream:
            async for event in run_stream:
                if isinstance(event, ApprovalNeededEvent):
                    requests.append(event)
                    await session.approvals.answer(
                        event.request_id,
                        ApprovalAnswer.ALWAYS if len(requests) == 1 else ApprovalAnswer.DENY,
                    )
        assert len(requests) == 2
        assert len(effects) == 2
        assert not session.approvals.grants
        child_id = session.children[0].id
    async with child.session(id=child_id) as session:
        assert not session.approvals.grants
    from harnessed.events import ApprovalResolvedEvent

    decisions = [
        change.event.decision
        for commit in store.commits
        for change in commit.changes
        if isinstance(change.event, ApprovalResolvedEvent)
    ]
    assert decisions == [ApprovalAnswer.ALLOW, ApprovalAnswer.ALLOW, ApprovalAnswer.DENY]


async def test_parent_grants_do_not_authorize_a_sibling_child():
    class Write(Plugin):
        @tool
        async def write(self) -> str:
            return "Written"

    child = Agent(
        model=ScriptedModel(
            [
                [ModelChunk(tool_call=ToolCall("write"))],
                "One",
                [ModelChunk(tool_call=ToolCall("write"))],
                "Two",
            ]
        ),
        plugins=[Write()],
    )
    parent = coordinator(child, [delegate(), delegate()], max_parallel=1)
    requests = []
    async with parent.session() as session:
        async with session.stream("Delegate") as run_stream:
            async for event in run_stream:
                if isinstance(event, ApprovalNeededEvent):
                    requests.append(event)
                    await session.approvals.answer(event.request_id, ApprovalAnswer.ALWAYS)
        assert len(requests) == 2
        assert len(session.approvals.grants) == 2
        assert len({item.request.scope for item in session.approvals.grants}) == 2


async def test_stable_call_identity_returns_recorded_receipt_without_starting_child_twice():
    call = delegate(id="stable-call")
    child_model = ScriptedModel(["Only child answer"])
    child = Agent(model=child_model)
    parent = Agent(
        model=ScriptedModel(
            [[ModelChunk(tool_call=call)], "One", [ModelChunk(tool_call=call)], "Two"]
        ),
        plugins=[Subagents({"review": child}), Approvals(default=PolicyDecision.ALLOW)],
    )
    async with parent.session() as session:
        first = await session.run("One")
        second = await session.run("Two")
        assert first.tool_calls[0][1] == second.tool_calls[0][1]
        assert len(session.children) == 1
        assert len(child_model.requests) == 1


async def test_child_projection_rejects_invalid_receipt_sequences():

    started = SubagentStartedEvent("child", "review", "task", "turn", "call", 1)
    finished = SubagentFinishedEvent("child", SubagentStatus.COMPLETED)
    log = SessionLog("parent")
    with pytest.raises(ValueError):
        await log.append(finished)
    await log.append(started)
    with pytest.raises(ValueError):
        await log.append(started)
    await log.append(finished)
    with pytest.raises(ValueError):
        await log.append(finished)


async def test_repeated_cancellation_does_not_interrupt_child_cleanup():
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()

    class Resource(Plugin):
        async def setup(self, scope):
            async def cleanup():
                cleaning.set()
                await asyncio.wait_for(release.wait(), timeout=5)
                cleaned.set()

            scope.effect(cleanup)

    async def blocked(request):
        started.set()
        await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
        yield ModelChunk()

    child = Agent(model=CallbackModel(blocked), plugins=[Resource()])
    parent = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=delegate())], "Done"]),
        plugins=[Approvals(default=PolicyDecision.ALLOW)],
    )
    async with parent.session() as session:
        mount = await session.mount(Subagents({"review": child}), section_delivery="context")
        handle = await session.submit("Delegate")
        await asyncio.wait_for(started.wait(), 2)
        unmount = asyncio.create_task(mount.aclose())
        await asyncio.wait_for(cleaning.wait(), 2)
        unmount.cancel()
        await asyncio.sleep(0)
        unmount.cancel()
        await asyncio.sleep(0)
        assert not unmount.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(unmount, timeout=2)
        assert cleaned.is_set()
        result = await asyncio.wait_for(handle.result(), 2)
        assert result.tool_calls[0][1].status is ToolStatus.CANCELLED


async def test_child_factory_rejects_existing_stored_conversation():
    from harnessed.core.approvals import CallbackApprover
    from harnessed.errors import ConfigError
    from harnessed.plugins.subagents import ChildSessionRequest

    child = Agent(model=ScriptedModel(["Existing answer"]), store=RecordingStore())
    async with child.session(id="occupied") as session:
        await session.run("Existing task")
        original = session.snapshot()
    request = ChildSessionRequest(
        "occupied",
        SubagentLinkedEvent("parent", "turn", "call", "review", 1, 1),
        CallbackApprover(lambda request: True),
    )
    with pytest.raises(ConfigError, match="existing child"):
        async with child.child_session(request):
            pytest.fail("An existing session must not be opened as a fresh child")
    async with child.session(id="occupied") as session:
        assert session.snapshot() == original


async def test_reused_call_identity_cannot_change_its_task():
    child_model = ScriptedModel(["Original answer"])
    parent = Agent(
        model=ScriptedModel(
            [
                [ModelChunk(tool_call=delegate(task="Original", id="same"))],
                "One",
                [ModelChunk(tool_call=delegate(task="Changed", id="same"))],
                "Two",
            ]
        ),
        plugins=[
            Subagents({"review": Agent(model=child_model)}),
            Approvals(default=PolicyDecision.ALLOW),
        ],
    )
    async with parent.session() as session:
        await session.run("One")
        second = await session.run("Two")
        assert second.tool_calls[0][1].status is ToolStatus.ERROR
        assert "different agent or task" in second.tool_calls[0][1].text
        assert len(child_model.requests) == 1


async def test_snapshot_restoration_preserves_receipts_call_limits_and_parent_links():
    child_store = RecordingStore()
    child_model = ScriptedModel([[ModelChunk(text="Child answer", usage=ModelUsage(7))]])
    child = Agent(model=child_model, store=child_store)
    parent = coordinator(child, [delegate(id="stable")], max_calls=1)
    first = await parent.run("Delegate once")
    state = SessionState.from_json(first.state.to_json())
    restored = Agent(
        model=ScriptedModel(
            [
                [ModelChunk(tool_call=delegate(id="stable")), ModelChunk(tool_call=delegate())],
                "Restored",
            ]
        ),
        plugins=[Subagents({"review": child}, max_calls=1), Approvals(allow=[Subagents.delegate])],
    )
    async with restored.session(state=state) as session:
        result = await session.run("Continue")
        assert session.children == first.state.children
        assert session.tree_usage.total_tokens == 7
        assert result.tool_calls[0][1] == first.tool_calls[0][1]
        assert "call limit" in result.tool_calls[1][1].text
        assert len(child_model.requests) == 1
    child_state = child_store.rows[state.children[0].id].checkpoint.state
    assert SessionState.from_json(child_state.to_json()).parent == child_state.parent
    assert child_state.parent is not None
    assert child_state.parent.parent_call_id == "stable"


async def test_start_and_receipt_are_committed_before_crossing_session_boundaries():
    parent_store, child_store = RecordingStore(), RecordingStore()

    async def child_response(request):
        parent_checkpoint = parent_store.rows["parent"].checkpoint
        starts = [e for e in parent_checkpoint.events if isinstance(e, SubagentStartedEvent)]
        assert len(starts) == 1
        child_checkpoint = child_store.rows[starts[0].child_session_id].checkpoint
        assert child_checkpoint.state.parent is not None
        assert child_checkpoint.state.parent.parent_session_id == "parent"
        yield ModelChunk(text="Child answer")

    calls = 0

    async def parent_response(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield ModelChunk(tool_call=delegate())
        else:
            receipts = [
                change.event
                for commit in parent_store.commits
                for change in commit.changes
                if isinstance(change.event, SubagentFinishedEvent)
            ]
            assert len(receipts) == 1
            assert receipts[0].text == "Child answer"
            yield ModelChunk(text="Parent answer")

    child = Agent(model=CallbackModel(child_response), store=child_store)
    parent = Agent(
        model=CallbackModel(parent_response),
        store=parent_store,
        plugins=[Subagents({"review": child}), Approvals(allow=[Subagents.delegate])],
    )
    async with parent.session(id="parent") as session:
        result = await session.run("Delegate")
        assert result.text == "Parent answer"
        assert result.state.children[0].status is SubagentStatus.COMPLETED
