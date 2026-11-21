from enum import StrEnum

import pytest
from pydantic import ValidationError

from harnessed import Agent, Approvals, Decision, Plugin, tool
from harnessed.events import ApprovalRequestedEvent
from harnessed.hooks import ReviewToolResult
from harnessed.kernel import Next, hook
from harnessed.models import ToolResult
from harnessed.plugins import Filesystem
from harnessed.runtime.approval_policy import CallbackApprover
from harnessed.testing import ScriptedModel, call
from harnessed.tools import tool_name


class Echo(Plugin):
    @tool(name="echo_text", readonly=True)
    async def echo(self, text: str) -> str:
        return text


async def test_explicit_ask_overrides_allow_readonly_and_policy():
    requests = []

    def approve(request):
        requests.append(request)
        return Decision.ALLOW

    model = ScriptedModel([call(Echo.echo, text="hello"), "done"])
    agent = Agent(
        model=model,
        approvals=Approvals(
            default=Decision.ALLOW,
            ask=[Echo.echo],
            policy=lambda call: Decision.ALLOW,
            approver=approve,
        ),
        plugins=[Echo()],
    )
    result = await agent.run("Echo")
    assert result.tool_calls[0][1].text == "hello"
    assert len(requests) == 1
    assert isinstance(requests[0], ApprovalRequestedEvent)
    assert requests[0].call.name == "echo_text"


async def test_denied_reference_never_asks():
    model = ScriptedModel([call(Echo.echo, text="hello"), "done"])
    agent = Agent(model=model, approvals=Approvals(deny=[Echo.echo]), plugins=[Echo()])
    result = await agent.run("Echo")
    assert result.tool_calls[0][1].is_error


async def test_unmounted_reference_and_wrong_recipe_fail_at_session_start():
    for plugins, policy in (
        ([], Approvals(ask=[Echo.echo])),
        ([Echo()], Approvals(ask=[Echo().echo])),
    ):
        agent = Agent(model=ScriptedModel([]), plugins=plugins, approvals=policy)
        with pytest.raises(ValueError, match="not registered"):
            async with agent.session():
                pytest.fail("unresolved references must fail before exposing the session")


async def test_bound_recipe_reference_survives_per_session_mount_copy():
    echo = Echo()
    model = ScriptedModel([call(echo.echo, text="hello"), "done"])
    agent = Agent(
        model=model,
        approvals=Approvals(ask=[echo.echo], approver=lambda request: Decision.ALLOW),
        plugins=[echo],
    )
    assert (await agent.run("Echo")).tool_calls[0][1].text == "hello"


def test_scripted_references_validate_arguments_without_self_or_injected_services():
    response = call(Filesystem.read_file, path="large.log", offset=40, limit=40)
    assert response.message.tool_calls[0].name == "read_file"
    with pytest.raises(ValidationError):
        call(Filesystem.read_file, path="large.log", limit="40")
    with pytest.raises(ValidationError):
        call(Filesystem.read_file, path="large.log", limit=0)
    with pytest.raises(ValidationError):
        call(Filesystem.read_file, path="large.log", fs="injected")


async def test_callback_adapter_receives_the_same_request_as_policy_callbacks():
    request = ApprovalRequestedEvent(
        "request", call(Echo.echo, text="hi").message.tool_calls[0], "why", "test"
    )
    seen = []
    adapter = CallbackApprover(lambda value: seen.append(value) or True)
    assert await adapter.ask(request) is Decision.ALLOW
    assert seen == [request]


def test_string_sentinel_and_arbitrary_functions_are_rejected():
    with pytest.raises(TypeError, match="action enum"):
        Approvals(allow="all")
    with pytest.raises(TypeError, match="@tool"):
        Approvals(ask=[lambda: None])


class Publish(StrEnum):
    POST = "publish.post"


class Notes(Plugin):
    @tool(action=Publish.POST)
    async def post_note(self, text: str) -> str:
        return text


class Matches(Plugin):
    def __init__(self, seen: list[tuple[str, bool, bool, bool]]) -> None:
        super().__init__()
        self.seen = seen

    @hook(ReviewToolResult)
    async def review(self, event: ReviewToolResult, next_: Next[ToolResult]) -> ToolResult:
        self.seen.append(
            (
                event.call.name,
                event.matches(Echo.echo),
                event.matches(Publish.POST),
                event.matches(Notes.post_note),
            )
        )
        return await next_()


async def test_tool_hooks_match_registrations_by_method_and_action_not_by_name():
    seen: list[tuple[str, bool, bool, bool]] = []
    model = ScriptedModel(
        [call(Echo.echo, text="a"), call(Notes.post_note, text="b"), call("missing"), "done"]
    )
    agent = Agent(model=model, plugins=[Echo(), Notes(), Matches(seen)])
    await agent.run("Use the tools")
    assert seen == [
        ("echo_text", True, False, False),
        ("post_note", False, True, True),
        ("missing", False, False, False),
    ]


async def test_session_tools_resolve_selectors_to_visible_registrations():
    assert tool_name(Echo.echo) == "echo_text"
    async with Agent(model=ScriptedModel([]), plugins=[Echo()]).session() as session:
        info = session.tools.resolve(Echo.echo)
        assert info is not None
        assert info.definition.name == "echo_text"
        assert info.readonly
        assert session.tools.resolve(Notes.post_note) is None
        assert session.tools.resolve(Publish.POST) is None
        with pytest.raises(TypeError, match="action enum"):
            session.tools.resolve("echo_text")
