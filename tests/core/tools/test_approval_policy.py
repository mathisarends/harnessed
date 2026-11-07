import pytest

from harnessed import Agent, Plugin, PolicyDecision, ToolCall, tool
from harnessed.core.tools.registry import ToolRegistry
from harnessed.events import PolicySource, ToolPolicyAppliedEvent
from harnessed.hooks import ToolPolicy
from harnessed.plugins import Approvals
from harnessed.services import SessionContext
from harnessed.testing import ScriptedModel, call


@pytest.mark.parametrize(
    "allow,ask,deny,confirm,readonly,policy,default,expected,policy_called",
    [
        (True, True, True, True, False, None, PolicyDecision.ALLOW, PolicyDecision.DENY, False),
        (
            True,
            True,
            False,
            False,
            True,
            PolicyDecision.DENY,
            PolicyDecision.ALLOW,
            PolicyDecision.ASK,
            False,
        ),
        (
            True,
            False,
            False,
            True,
            False,
            PolicyDecision.DENY,
            PolicyDecision.ASK,
            PolicyDecision.ALLOW,
            False,
        ),
        (
            False,
            False,
            False,
            True,
            False,
            PolicyDecision.ALLOW,
            PolicyDecision.ALLOW,
            PolicyDecision.ASK,
            False,
        ),
        (
            False,
            False,
            False,
            False,
            True,
            PolicyDecision.DENY,
            PolicyDecision.DENY,
            PolicyDecision.ALLOW,
            False,
        ),
        (
            False,
            False,
            False,
            False,
            False,
            PolicyDecision.DENY,
            PolicyDecision.ALLOW,
            PolicyDecision.DENY,
            True,
        ),
        (False, False, False, False, False, None, PolicyDecision.ASK, PolicyDecision.ASK, True),
        (False, False, False, False, False, None, PolicyDecision.ALLOW, PolicyDecision.ALLOW, True),
        (
            False,
            False,
            False,
            False,
            False,
            PolicyDecision.ASK,
            PolicyDecision.ALLOW,
            PolicyDecision.ASK,
            True,
        ),
        (False, False, False, False, False, None, PolicyDecision.DENY, PolicyDecision.DENY, True),
    ],
)
async def test_approval_precedence(
    allow: bool,
    ask: bool,
    deny: bool,
    confirm: bool,
    readonly: bool,
    policy: PolicyDecision | None,
    default: PolicyDecision,
    expected: PolicyDecision,
    policy_called: bool,
) -> None:
    asked, executed, policies = [], [], []

    class Work(Plugin):
        @tool(confirm=confirm, readonly=readonly)
        def work(self) -> str:
            executed.append(True)
            return "done"

    def decide(request: ToolCall) -> PolicyDecision | None:
        policies.append(request)
        return policy

    approvals = Approvals(
        allow=[Work.work] if allow else (),
        ask=[Work.work] if ask else (),
        deny=[Work.work] if deny else (),
        policy=decide,
        default=default,
        approver=lambda request: asked.append(request) or True,
    )
    async with Agent(
        model=ScriptedModel([call(Work.work), "finished"]),
        plugins=[Work(), approvals],
    ).session() as session:
        result = await session.run("Work")
        applied = session.last(ToolPolicyAppliedEvent)
    assert bool(asked) is (expected is PolicyDecision.ASK)
    assert bool(policies) is policy_called
    assert bool(executed) is (expected is not PolicyDecision.DENY)
    assert result.tool_calls[0][1].is_error is (expected is PolicyDecision.DENY)
    assert applied is not None
    assert applied.decision is (
        PolicyDecision.DENY if expected is PolicyDecision.DENY else PolicyDecision.ALLOW
    )
    assert applied.source is (
        PolicySource.APPROVAL if expected is PolicyDecision.ASK else PolicySource.POLICY
    )


def test_convenience_policies_and_conflicting_tool_flags():
    assert Approvals.allow_all().default is PolicyDecision.ALLOW
    assert Approvals.ask_all().default is PolicyDecision.ASK
    with pytest.raises(TypeError, match="cannot be combined"):
        tool(readonly=True, confirm=True)(lambda: "unused")


async def test_approval_uses_the_prepared_tool_flags_after_replacement():
    asked = []

    class Work(Plugin):
        @tool(confirm=True)
        def work(self) -> str:
            return "original"

    def replacement() -> str:
        return "replacement"

    async with Agent(
        model=ScriptedModel([]),
        plugins=[
            Work(),
            Approvals.allow_all(approver=lambda request: asked.append(request) or True),
        ],
    ).session() as session:
        registry = session.scope[ToolRegistry]
        plan = registry.plan(ToolCall("work"))
        registry.register(replacement, name="work", override=True, readonly=True)
        assert plan.tool is not None and plan.tool.confirm
        decision = await session.scope.dispatch(
            ToolPolicy(plan.call, session.scope[SessionContext], tool=plan.tool),
            default=lambda: PolicyDecision.ALLOW,
        )
    assert decision is PolicyDecision.ALLOW
    assert len(asked) == 1
