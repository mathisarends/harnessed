import asyncio

import pytest

from harnessed import Agent as Application
from harnessed import AgentLoop, Approvals, Plugin, ToolContext, tool
from harnessed.approvals.policy import ApprovalsConfig, ApprovalState
from harnessed.approvals.responders import Approver, CallbackApprover
from harnessed.errors import ApprovalUnavailable
from harnessed.events import (
    ApprovalResolvedEvent,
    ApprovalStatus,
    ToolCallFinishedEvent,
    ToolExecutionStartedEvent,
)
from harnessed.events.codec import decode_event, encode_event
from harnessed.kernel import Inject, hook
from harnessed.models import (
    ApprovalResponse,
    ApprovalSource,
    Decision,
    ModelChunk,
    ToolCall,
    ToolStatus,
)
from harnessed.plugins import Service
from harnessed.runtime.hooks import RequestApproval, ToolPolicy
from harnessed.testing import ScriptedModel
from tests._audit import Audit, AuditProjection


async def test_removing_an_approver_never_removes_the_scoped_guard():
    from harnessed import Plugin, tool
    from harnessed.approvals.responders import CallbackApprover
    from harnessed.plugins import Service
    from harnessed.testing import call

    effects = []

    class Publish(Plugin):
        @tool
        async def publish(self) -> str:
            effects.append("published")
            return "ready"

    model = ScriptedModel(
        [call("publish"), "first", call("publish"), "blocked", call("publish"), "third"]
    )
    async with Application(
        model=model, approvals=Approvals(ask=[Publish.publish]), plugins=[Publish()]
    ).session() as session:
        provider = await session.mount(Service(Approver, CallbackApprover(lambda call: True)))
        assert (await session.run("publish")).tool_calls[0][1].status == ToolStatus.OK
        await provider.aclose()
        await session.scope.settle()
        assert (await session.run("publish")).tool_calls[0][1].status == ToolStatus.UNAVAILABLE
        await session.mount(Service(Approver, CallbackApprover(lambda call: True)))
        assert (await session.run("publish")).tool_calls[0][1].status == ToolStatus.OK
        assert effects == ["published", "published"]


async def test_approval_gate_without_a_ui_fails_closed_and_preserves_unrelated_calls():
    executed = []

    class ToolsPlugin(Plugin):
        @tool
        async def risky(self) -> str:
            pytest.fail("Approval gate disappeared without a UI")

        @tool
        async def safe(self) -> str:
            executed.append("safe")
            return "ok"

    tools = ToolsPlugin(name="headless")

    calls = [ToolCall("risky"), ToolCall("safe")]
    model = ScriptedModel([[ModelChunk(tool_call=call) for call in calls], "done"])
    async with Application(
        model=model, approvals=Approvals(default=Decision.ALLOW, ask={tools.risky}), plugins=[tools]
    ).session() as agent:
        agent_audit = Audit(agent)
        await asyncio.wait_for(agent.run("go"), 2)
        results = [
            event.result for event in agent_audit.events if isinstance(event, ToolCallFinishedEvent)
        ]
        assert [result.status for result in results] == ["unavailable", "ok"]
        assert "No approval responder" in results[0].content
        assert executed == ["safe"]
        approvals = ApprovalState(agent_audit.events)
        assert not approvals.pending and (not approvals.grants)
        assert approvals.records[0].resolution.status == ApprovalStatus.UNAVAILABLE
        assert approvals.records[0].decision is None
        replay = AuditProjection(tuple(decode_event(encode_event(e)) for e in agent_audit.events))
        assert replay.approvals == approvals


async def test_direct_sdk_approval_raises_typed_unavailable_and_leaves_no_pending_question():
    async with Application(
        model=ScriptedModel([]), approvals=Approvals(default=Decision.ALLOW)
    ).session() as agent:
        with pytest.raises(ApprovalUnavailable, match="No approval responder"):
            await asyncio.wait_for(agent.approvals.request(ToolCall("work"), "permission"), 2)
        record = agent.log.approvals.records[-1]
        assert record.resolution.status == ApprovalStatus.UNAVAILABLE and record.decision is None
        assert not agent.log.approvals.pending


async def test_explicit_approver_then_service_take_precedence_over_request_listeners():

    class Answer:
        def __init__(self, decision):
            self.decision = decision

        async def ask(self, request):
            return self.decision

    async with Application(
        model=ScriptedModel([]), approvals=Approvals(default=Decision.ALLOW)
    ).session() as agent:
        agent.scope.provide(Approver, Answer(Decision.DENY))
        agent.scope.on(RequestApproval, lambda ev: pytest.fail("Explicit approver was bypassed"))
        assert (
            await agent.approvals.request(
                ToolCall("work"), "permission", approver=Answer(Decision.ALLOW)
            )
            is Decision.ALLOW
        )
        assert await agent.approvals.request(ToolCall("work"), "permission") is Decision.DENY


async def test_approval_listeners_bail_by_priority_and_can_explicitly_abstain_or_deny():
    asked = []
    async with Application(
        model=ScriptedModel([]), approvals=Approvals(default=Decision.ALLOW)
    ).session() as agent:
        agent.scope.on(RequestApproval, lambda ev: asked.append("late"), priority=100)

        def answer(event):
            assert event.call.name == "work" and event.reason == "permission"
            assert event.request.request_id in {
                item.request_id for item in event.session.log.approvals.pending
            }
            asked.append("deny")
            return Decision.DENY

        remove_answer = agent.scope.on(RequestApproval, answer, priority=0)
        agent.scope.on(RequestApproval, lambda ev: asked.append("abstain"), priority=-100)
        assert await agent.approvals.request(ToolCall("work"), "permission") is Decision.DENY
        assert asked == ["abstain", "deny"]
        await remove_answer()
        asked.clear()
        with pytest.raises(ApprovalUnavailable):
            await agent.approvals.request(ToolCall("work"), "permission")
        assert asked == ["abstain", "late"]


@pytest.mark.parametrize("transport", ["listener", "service", "callback"])
async def test_unresponsive_approval_uses_the_session_timeout_and_cancels_the_responder(transport):
    stopped = asyncio.Event()

    async def wait(*args):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    class WaitingApprover:
        ask = staticmethod(wait)

    plugins = [Service(Approver, CallbackApprover(wait))] if transport == "callback" else []
    harness = Application(
        model=ScriptedModel([]),
        approvals=Approvals(default=Decision.ALLOW, timeout_seconds=0.02),
        plugins=[*plugins],
    )
    async with harness.session() as agent:
        if transport == "listener":
            agent.scope.on(RequestApproval, wait)
        elif transport == "service":
            agent.scope.provide(Approver, WaitingApprover())
        with pytest.raises(ApprovalUnavailable, match=r"timed out after 0.02 seconds"):
            await asyncio.wait_for(agent.approvals.request(ToolCall("work"), "permission"), 2)
        assert stopped.is_set()
        assert not agent.log.approvals.pending
        assert agent.last(ApprovalResolvedEvent).status == ApprovalStatus.UNAVAILABLE


@pytest.mark.parametrize("answer", [None, Decision.ASK, "allow", ValueError("disconnected")])
async def test_responder_abstention_invalid_answers_and_failures_are_unavailable(answer):

    async def respond(event):
        if isinstance(answer, Exception):
            raise answer
        return answer

    async with Application(
        model=ScriptedModel([]), approvals=Approvals(default=Decision.ALLOW)
    ).session() as agent:
        agent.scope.on(RequestApproval, respond)
        with pytest.raises(ApprovalUnavailable):
            await agent.approvals.request(ToolCall("work"), "permission")
        record = agent.log.approvals.records[-1]
        assert record.resolution.status == ApprovalStatus.UNAVAILABLE and record.resolution.reason
        assert not record.pending and (not record.is_grant)


async def test_gate_stays_active_after_approver_service_is_removed():
    executed = []

    class ToolsPlugin(Plugin):
        @tool
        async def work(self) -> str:
            executed.append(True)
            return "ok"

    tools = ToolsPlugin(name="tools")

    class AllowOnce:
        async def ask(self, request):
            return Decision.ALLOW

    model = ScriptedModel(
        [
            [ModelChunk(tool_call=ToolCall("work"))],
            "first",
            [ModelChunk(tool_call=ToolCall("work"))],
            "second",
        ]
    )
    async with Application(
        model=model, approvals=Approvals(default=Decision.ALLOW, ask={tools.work}), plugins=[tools]
    ).session() as agent:
        agent_audit = Audit(agent)
        remove = agent.scope.provide(Approver, AllowOnce())
        await agent.run("first")
        await remove()
        await agent.scope.settle()
        await agent.run("second")
        assert executed == [True]
        assert agent.last(ApprovalResolvedEvent).status == ApprovalStatus.UNAVAILABLE
        assert len([e for e in agent_audit.events if isinstance(e, ToolExecutionStartedEvent)]) == 1


async def test_approval_wait_occurs_after_policy_hooks():
    clock, measured, order = ([0], [], [])

    class ToolsPlugin(Plugin):
        @tool
        async def work(self) -> str:
            return "ok"

        @hook(ToolPolicy, priority=0)
        async def timing(self, event, next_):
            order.append("timer")
            before = clock[0]
            result = await next_()
            measured.append(clock[0] - before)
            return result

        @hook(ToolPolicy, priority=100)
        async def policy(self, event, next_):
            order.append("policy")
            clock[0] += 2
            return await next_()

    tools = ToolsPlugin(name="timing")

    async def answer(call):
        order.append("approval")
        clock[0] += 100
        return Decision.ALLOW

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "done"])
    async with Application(
        model=model, approvals=Approvals(ask=[tools.work], approver=answer), plugins=[tools]
    ).session() as agent:
        await agent.run("go")
    assert order == ["timer", "policy", "approval"]
    assert measured == [2]


@pytest.mark.parametrize("timeout_seconds", [0, -1, True, float("nan"), float("inf"), "5"])
def test_approval_timeout_must_be_finite_and_positive(timeout_seconds):
    with pytest.raises(ValueError, match="timeout"):
        Approvals(timeout_seconds=timeout_seconds)
    with pytest.raises(ValueError, match="timeout"):
        ApprovalsConfig(timeout_seconds=timeout_seconds)


def test_agent_loop_has_no_second_approval_timeout():
    with pytest.raises(TypeError, match="approval_timeout"):
        AgentLoop(approval_timeout=1)


@pytest.mark.parametrize("timeout_seconds", [0, -1, True, float("nan"), float("inf"), "5"])
async def test_invalid_request_timeout_does_not_record_a_question(timeout_seconds):
    async with Application(model=ScriptedModel([])).session() as session:
        with pytest.raises(ValueError, match="approval_timeout"):
            await session.approvals.request(
                ToolCall("work"), "permission", timeout_seconds=timeout_seconds
            )
        assert session.approvals.records == ()


@pytest.mark.parametrize("default,override", [(0.01, 1.0), (1.0, 0.01)])
async def test_request_timeout_replaces_the_default_in_either_direction(default, override):
    async def respond(request):
        await asyncio.sleep(0.05)
        return Decision.ALLOW

    async with Application(
        model=ScriptedModel([]),
        approvals=Approvals(default=Decision.ASK, approver=respond, timeout_seconds=default),
    ).session() as session:
        if override > default:
            assert (
                await session.approvals.request(
                    ToolCall("work"), "permission", timeout_seconds=override
                )
                is Decision.ALLOW
            )
        else:
            with pytest.raises(ApprovalUnavailable, match=r"timed out after 0.01 seconds"):
                await session.approvals.request(
                    ToolCall("work"), "permission", timeout_seconds=override
                )
        assert not session.approvals.pending


async def test_none_inherits_the_default_and_an_override_does_not_change_the_next_question():
    async def respond(request):
        await asyncio.sleep(0.05)
        return Decision.ALLOW

    async with Application(
        model=ScriptedModel([]),
        approvals=Approvals(default=Decision.ASK, approver=respond, timeout_seconds=0.01),
    ).session() as session:
        assert (
            await session.approvals.request(ToolCall("work"), "permission", timeout_seconds=1)
            is Decision.ALLOW
        )
        with pytest.raises(ApprovalUnavailable, match=r"timed out after 0.01 seconds"):
            await session.approvals.request(ToolCall("work"), "permission", timeout_seconds=None)
        assert not session.approvals.pending


async def test_replacing_approval_configuration_changes_only_new_questions():
    asked = asyncio.Event()
    answer = asyncio.Event()

    async def respond(request):
        asked.set()
        await answer.wait()
        return Decision.ALLOW

    async with Application(
        model=ScriptedModel([]),
        approvals=Approvals(default=Decision.ASK, approver=respond, timeout_seconds=0.03),
    ).session() as session:
        async with asyncio.timeout(2):
            waiting = asyncio.create_task(session.approvals.request(ToolCall("work"), "permission"))
            await asked.wait()
            with pytest.raises(TypeError, match="Plugin instance"):
                await session.mount(
                    Approvals(default=Decision.ASK, approver=respond, timeout_seconds=1)
                )
            with pytest.raises(ApprovalUnavailable, match=r"timed out after 0.03 seconds"):
                await waiting
            answer.set()
            assert await session.approvals.request(ToolCall("work"), "permission") is Decision.ALLOW
        assert not session.approvals.pending


@pytest.mark.parametrize("source", ["policy", "rule", "tool", "hook"])
async def test_all_approval_sources_use_the_configured_response_timeout(source):
    executed = []
    stopped = asyncio.Event()

    class Work(Plugin):
        @tool
        async def work(self, *, context: Inject[ToolContext]) -> str:
            if source == "tool":
                await context.approve("permission")
            executed.append("work")
            return "done"

    async def respond(request):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    approvals = Approvals(
        default=Decision.ASK if source == "policy" else Decision.ALLOW,
        ask=[Work.work] if source == "rule" else (),
        approver=respond,
        timeout_seconds=0.02,
    )
    async with Application(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "done"]),
        approvals=approvals,
        plugins=[Work()],
    ).session() as session:
        if source == "hook":
            session.scope.on(ToolPolicy, lambda event, next_: Decision.ASK)
        audit = Audit(session)
        async with asyncio.timeout(2):
            result = await session.run("work")
        assert executed == [] and stopped.is_set()
        assert result.tool_calls[0][1].status is ToolStatus.UNAVAILABLE
        resolution = ApprovalState(audit.events).records[-1].resolution
        assert resolution.status is ApprovalStatus.UNAVAILABLE
        assert resolution.reason == "Approval response timed out after 0.02 seconds"
        assert not session.approvals.pending


def test_unavailable_resolution_is_not_an_answer_or_a_persistent_permission():
    with pytest.raises(ValueError):
        ApprovalResolvedEvent("request", Decision.ALLOW, status=ApprovalStatus.UNAVAILABLE)
    with pytest.raises(TypeError):
        ApprovalResolvedEvent("request", Decision.ALLOW, reason=123)


def test_reviewer_allow_cannot_become_a_remembered_grant():
    with pytest.raises(ValueError, match="one request"):
        ApprovalResponse(Decision.ALLOW, source=ApprovalSource.REVIEWER, remember=True)
    with pytest.raises(ValueError, match="one request"):
        ApprovalResolvedEvent(
            "request", Decision.ALLOW, source=ApprovalSource.REVIEWER, remember=True
        )
