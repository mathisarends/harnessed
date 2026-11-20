from enum import StrEnum

import pytest

from harnessed import Agent, Approvals, Decision, Plugin, tool
from harnessed.models import ToolCall
from harnessed.testing import ScriptedModel, call
from harnessed.tools import ToolRegistry


class DocumentAction(StrEnum):
    PUBLISH = "publish"


class OtherAction(StrEnum):
    PUBLISH = "publish"


class Documents(Plugin):
    @tool(action=DocumentAction.PUBLISH, name="publish_revision")
    def send(self) -> str:
        return "published"

    @tool(readonly=True, action=OtherAction.PUBLISH)
    def inspect(self) -> str:
        return "read"


async def test_actions_survive_method_renames_and_strict_defaults_cover_readonly():
    result = await Agent(
        model=ScriptedModel([call(Documents.send), call(Documents.inspect), "done"]),
        plugins=[Documents()],
        approvals=Approvals(allow=[DocumentAction.PUBLISH]),
    ).run("publish")
    assert [item.status for _, item in result.tool_calls] == ["ok", "denied"]


def test_conflicting_rules_require_an_unambiguous_decision():
    with pytest.raises(ValueError, match="Conflicting"):
        Approvals(allow=[DocumentAction.PUBLISH], ask=[DocumentAction.PUBLISH])
    Approvals(allow=[DocumentAction.PUBLISH], deny=[OtherAction.PUBLISH])


async def test_method_policy_is_bound_to_the_prepared_registration():
    policy = Approvals(allow=[Documents.send], default=Decision.DENY)
    async with Agent(
        model=ScriptedModel([]), plugins=[Documents()], approvals=policy
    ).session() as session:
        registry = session.scope[ToolRegistry]
        original = registry.plan(ToolCall("publish_revision"))
        registry.register(lambda: "replacement", name="publish_revision", override=True)
        replacement = registry.plan(ToolCall("publish_revision"))
        assert policy.decide(original) is Decision.ALLOW
        assert policy.decide(replacement) is Decision.DENY


@pytest.mark.parametrize(
    ("policy", "statuses"),
    [
        (None, ["ok", "ok"]),
        (Approvals(), ["ok", "ok"]),
        (Approvals(allow=[]), ["denied", "denied"]),
        (Approvals(deny=[DocumentAction.PUBLISH]), ["denied", "ok"]),
        (Approvals(ask=[DocumentAction.PUBLISH]), ["unavailable", "ok"]),
    ],
)
async def test_omitted_empty_and_selective_rules(policy, statuses):
    model = ScriptedModel([call(Documents.send), call(Documents.inspect), "done"])
    agent = Agent(model=model, plugins=[Documents()], approvals=policy)
    result = await agent.run("check")
    assert [output.status for _, output in result.tool_calls] == statuses


async def test_remembered_action_covers_new_wire_name_but_not_an_unrelated_registration():
    class Exports(Plugin):
        @tool(action=DocumentAction.PUBLISH)
        def export(self) -> str:
            return "exported"

    requests = []

    def respond(request):
        requests.append(request.call.name)
        return request.allow(remember=True)

    model = ScriptedModel(
        [call(Documents.send), call(Exports.export), call(Documents.inspect), "done"]
    )
    agent = Agent(
        model=model,
        plugins=[Documents(), Exports()],
        approvals=Approvals(default=Decision.ASK, approver=respond),
    )
    result = await agent.run("publish and inspect")
    assert [item.status for _, item in result.tool_calls] == ["ok", "ok", "ok"]
    assert requests == ["publish_revision", "inspect"]
