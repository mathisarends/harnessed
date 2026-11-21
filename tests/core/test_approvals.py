import asyncio

import pytest

from harnessed import Agent as Application
from harnessed import Approvals, Plugin, tool
from harnessed.approvals.policy import ApprovalState
from harnessed.approvals.responders import Approver, CallbackApprover
from harnessed.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
    ApprovalStatus,
    ToolCallFinishedEvent,
    ToolExecutionStartedEvent,
    ToolPolicyAppliedEvent,
)
from harnessed.kernel import hook
from harnessed.models import Decision, ModelChunk, ToolCall
from harnessed.plugins import Service
from harnessed.runtime.hooks import ToolPolicy
from harnessed.testing import ScriptedModel
from tests._audit import Audit, AuditProjection


def tool_model():
    return ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "done"])


async def test_grants_are_cached_only_in_the_live_session_and_not_in_imported_state():
    asked = []

    class Work(Plugin):
        @tool
        def work(self) -> str:
            return "ok"

    def answer(request):
        asked.append(request.request_id)
        return request.allow(remember=True)

    agent = Application(
        model=ScriptedModel(
            [
                [ModelChunk(tool_call=ToolCall("work"))],
                "first",
                [ModelChunk(tool_call=ToolCall("work"))],
                "second",
                [ModelChunk(tool_call=ToolCall("work"))],
                "third",
            ]
        ),
        approvals=Approvals(default=Decision.ALLOW, ask={Work.work}, approver=answer),
        plugins=[Work()],
    )
    async with agent.session() as session:
        await session.run("First")
        grant = session.approvals.grants[0]
        await session.run("Second")
        assert len(asked) == 1
        state = session.snapshot()
        await session.approvals.revoke(grant.request_id)
        assert not session.approvals.grants
    async with agent.session(state=state) as session:
        assert not session.approvals.grants
        await session.run("Third")
        assert len(asked) == 2


@pytest.mark.parametrize("decision", [Decision.ALLOW, Decision.DENY])
async def test_single_call_answers_are_logged_without_becoming_persistent_grants(
    tmp_path, decision
):
    asked, executed = ([], [])

    class ToolsPlugin(Plugin):
        @tool
        async def work(self) -> str:
            executed.append(True)
            return "ok"

    tools = ToolsPlugin(name="work")

    def answer(request):
        call = request.call
        asked.append(call.id)
        return decision

    for _ in range(2):
        harness = Application(
            model=tool_model(),
            approvals=Approvals(default=Decision.ALLOW, ask={tools.work}, approver=answer),
            plugins=[tools],
        )
        async with harness.session() as agent:
            agent_audit = Audit(agent)
            await agent.run("task")
            assert not agent.log.approvals.grants
            assert ApprovalState(agent_audit.events).records[-1].decision is decision
            policy = next(
                e for e in reversed(agent_audit.events) if isinstance(e, ToolPolicyAppliedEvent)
            )
        assert policy.decision is Decision(decision.value)
    assert len(asked) == 2
    assert len(executed) == (2 if decision is Decision.ALLOW else 0)


async def test_gate_scopes_do_not_share_persistent_permissions(tmp_path):
    asked = []

    class ToolsPlugin(Plugin):
        @tool
        async def work(self) -> str:
            return "ok"

    tools = ToolsPlugin(name="work")

    def answer(request):
        call = request.call
        asked.append(call.id)
        return request.allow(remember=True)

    for _ in range(2):
        async with Application(
            model=tool_model(),
            approvals=Approvals(default=Decision.ALLOW, ask={tools.work}, approver=answer),
            plugins=[tools],
        ).session() as agent:
            await agent.run("task")
    assert len(asked) == 2


async def test_tool_policy_ask_uses_the_same_durable_approval_path(tmp_path):
    asked = []

    class ToolsPlugin(Plugin):
        @tool
        async def work(self) -> str:
            return "ok"

        @hook(ToolPolicy)
        async def ask_policy(self, event, next_):
            return Decision.ASK

    tools = ToolsPlugin(name="work")

    def answer(request):
        call = request.call
        asked.append(call.id)
        return request.allow(remember=True)

    for _ in range(2):
        async with Application(
            model=tool_model(),
            approvals=Approvals(default=Decision.ALLOW),
            plugins=[tools, Service(Approver, CallbackApprover(answer))],
        ).session() as agent:
            await agent.run("task")
            assert agent.log.approvals.grant_for("work", scope="tool-policy") is not None
    assert len(asked) == 2


async def test_cancellation_closes_an_unanswered_approval_and_does_not_execute_the_tool():
    asked = asyncio.Event()

    class ToolsPlugin(Plugin):
        @tool
        async def work(self) -> str:
            pytest.fail("Tool ran without an approval answer")

    tools = ToolsPlugin(name="work")

    async def wait(call):
        asked.set()
        await asyncio.Event().wait()

    async with Application(
        model=tool_model(),
        approvals=Approvals(default=Decision.ALLOW, ask={tools.work}, approver=wait),
        plugins=[tools],
    ).session() as agent:
        agent_audit = Audit(agent)
        await agent.submit("task")
        await asyncio.wait_for(asked.wait(), timeout=5)
        assert len(agent.log.approvals.pending) == 1
        await agent.abort()
        assert not agent.log.approvals.pending and (not agent.log.approvals.grants)
        assert (
            ApprovalState(agent_audit.events).records[-1].resolution.status
            == ApprovalStatus.INTERRUPTED
        )
        assert not any(isinstance(e, ToolExecutionStartedEvent) for e in agent_audit.events)
        assert any(
            isinstance(e, ToolCallFinishedEvent) and e.result.is_error for e in agent_audit.events
        )


@pytest.mark.parametrize(
    "events",
    [
        (ApprovalResolvedEvent("unknown", Decision.ALLOW),),
        (ApprovalRevokedEvent("unknown"),),
        (ApprovalRequestedEvent("id", ToolCall("work"), "reason", "scope", grant_id="missing"),),
        (
            ApprovalRequestedEvent("id", ToolCall("work"), "reason", "scope"),
            ApprovalResolvedEvent("id", Decision.DENY),
            ApprovalRevokedEvent("id"),
        ),
    ],
)
def test_invalid_approval_transitions_are_rejected_by_replay(events):
    with pytest.raises(ValueError):
        AuditProjection(events)
