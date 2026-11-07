import pytest

from harnessed import Agent, Plugin, tool
from harnessed.core.model import OutputSchema
from harnessed.hooks import Next, PrepareRequest
from harnessed.kernel import PluginStatus, hook
from harnessed.models import (
    GenerationSettings,
    ModelRequest,
    SystemMessage,
    ToolDefinition,
    UserMessage,
)
from harnessed.testing import ScriptedModel, call


def test_append_message_returns_a_new_request_and_preserves_its_contract():
    original = ModelRequest(
        (SystemMessage("Rules"), UserMessage("Task")),
        tools=(ToolDefinition("read", "Read a file", {"type": "object"}),),
        settings=GenerationSettings(max_tokens=42),
        output_schema=OutputSchema("answer", "Answer", {"type": "string"}),
    )
    message = UserMessage("Additional knowledge")
    extended = original.append_message(message)
    assert extended is not original
    assert [item.text for item in original.messages] == ["Rules", "Task"]
    assert extended.messages == (*original.messages, message)
    assert extended.tools == original.tools
    assert extended.settings == original.settings
    assert extended.output_schema == original.output_schema


async def test_request_hooks_compose_on_return_without_appending_to_conversation_history():
    class Outer(Plugin):
        @hook(PrepareRequest)
        async def enrich(self, event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
            return (await next_()).append_message(UserMessage("Outer knowledge"))

    class Inner(Plugin):
        @hook(PrepareRequest)
        async def enrich(self, event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
            return (await next_()).append_message(UserMessage("Inner knowledge"))

        @tool(readonly=True)
        async def read(self) -> str:
            return "Read result"

    model = ScriptedModel([call("read"), "answer"])
    async with Agent(model=model, plugins=[Outer(), Inner()]).session() as session:
        result = await session.run("Task")
        assert result.text == "answer"
        assert len(model.requests) == 2
        assert [item.text for item in model.requests[0].messages] == [
            "Task",
            "Inner knowledge",
            "Outer knowledge",
        ]
        assert [item.text for item in model.requests[1].messages][-2:] == [
            "Inner knowledge",
            "Outer knowledge",
        ]
        assert all("knowledge" not in item.text for item in session.messages)
        assert all("knowledge" not in entry.message.text for entry in result.state.history)


async def test_request_contributions_follow_mount_dependency_and_disposal_lifetimes():
    class Backend:
        pass

    class Guidance(Plugin, needs=(Backend,)):
        @hook(PrepareRequest)
        async def enrich(self, event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
            return (await next_()).append_message(UserMessage("Active backend guidance"))

    model = ScriptedModel(["waiting", "active", "suspended", "restarted", "removed"])
    async with Agent(model=model).session() as session:
        handle = await session.mount(Guidance())
        assert handle.status is PluginStatus.WAITING
        await session.run("Waiting")
        remove = session.scope.provide(Backend, Backend())
        await session.run("Active")
        await remove()
        await session.run("Suspended")
        session.scope.provide(Backend, Backend())
        await session.run("Restarted")
        await handle.aclose()
        await session.run("Removed")
    assert [
        any(message.text == "Active backend guidance" for message in request.messages)
        for request in model.requests
    ] == [False, True, False, True, False]


@pytest.mark.parametrize("operation", ["run", "submit", "stream"])
async def test_removed_context_keyword_is_rejected_before_input_admission(operation):
    model = ScriptedModel([])
    async with Agent(model=model).session() as session:
        with pytest.raises(TypeError, match="context"):
            if operation == "run":
                await session.run("Task", context={"ticket": "old"})
            elif operation == "submit":
                await session.submit("Task", context={"ticket": "old"})
            else:
                async with session.stream("Task", context={"ticket": "old"}):
                    pytest.fail("Removed keyword admitted a run")
        assert not session.inbox.pending
        assert not model.requests
