import asyncio
import gc
import weakref

import pytest

from harnessed import Agent, Approvals, Plugin, SystemPrompt, tool
from harnessed.events import (
    CompactionAppliedEvent,
    PluginInstructionsChangedEvent,
    ToolPolicyAppliedEvent,
    TurnEndedEvent,
    TurnStartedEvent,
)
from harnessed.kernel import PluginScope, needs
from harnessed.models import Decision, ModelChunk, ToolCall, ToolStatus
from harnessed.prompts import Instructions
from harnessed.prompts.plugin_instructions import PluginInstructions
from harnessed.sessions.log import SessionLog
from harnessed.testing import ScriptedModel


class Calculator(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt(Instructions(text="Use add for arithmetic."))

    @tool(readonly=True)
    async def add(self, a: int, b: int) -> int:
        """Add two integers."""
        return a + b


async def test_application_facade_composes_plugins_and_fixed_ordered_guidance(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("Project rules.")
    model = ScriptedModel(
        [[ModelChunk(tool_call=ToolCall("add", {"a": 19, "b": 23}))], "42", "done"]
    )
    calculator = Calculator()
    app = Agent(
        model=model,
        plugins=[calculator],
        instructions=SystemPrompt.from_plugins(
            Instructions(text="Be concise."),
            Instructions.from_file(path=path),
        ),
    )
    assert "Calculator.instructions" in str(app.prompt)
    async with app.session() as session:
        result = await session.run("Add")
        assert result.text == "42" and result.tool_calls[0][1].content == "42"
        assert (
            await session.prompt.render()
            == "Be concise.\n\nProject rules.\n\nUse add for arithmetic."
        )
        path.write_text("Changed externally.")
        await session.run("Again")
        assert (
            await session.prompt.render()
            == "Be concise.\n\nProject rules.\n\nUse add for arithmetic."
        )
    assert model.requests[0].messages[0].text == model.requests[-1].messages[0].text


async def test_model_parameter_is_equivalent_to_model_service():
    one, two = ScriptedModel(["done"]), ScriptedModel(["done"])
    calculator = Calculator()
    await Agent(
        model=one,
        plugins=[calculator],
        instructions=SystemPrompt.from_plugins(Instructions(text="Rules.")),
    ).run("Go")
    calculator_4 = Calculator()
    await Agent(
        instructions=SystemPrompt.from_plugins(Instructions(text="Rules.")),
        model=two,
        plugins=[calculator_4],
    ).run("Go")
    assert [message.text for message in one.requests[0].messages] == [
        message.text for message in two.requests[0].messages
    ]
    assert one.requests[0].tools == two.requests[0].tools


async def test_plugin_instruction_block_can_be_overridden_or_omitted():
    class CustomCalculator(Calculator):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt("Replacement guidance.")

    class QuietCalculator(Calculator):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt()

    for recipe, expected in [
        (CustomCalculator(), "Replacement guidance."),
        (QuietCalculator(), ""),
    ]:
        app = Agent(
            model=ScriptedModel([]),
            plugins=[recipe],
        )
        async with app.session() as session:
            assert await session.prompt.render() == expected


async def test_removed_runtime_plugins_are_collectable_while_the_session_stays_open():
    class Missing:
        pass

    async with Agent(model=ScriptedModel([])).session() as session:
        references = []
        for index in range(10):
            recipe = Plugin(needs=(Missing,) if index % 2 else ())
            handle = await session.mount(recipe)
            references.extend((weakref.ref(handle), weakref.ref(recipe)))
            await handle.aclose()
        del handle, recipe
        await session.scope.settle()
        gc.collect()
        assert all(reference() is None for reference in references)
        assert not session.scope[PluginInstructions].values


async def test_default_policy_allows_each_unrestricted_tool():
    class Mutating(Plugin):
        @tool
        async def write(self) -> str:
            return "written"

        @tool
        async def delete(self) -> str:
            return "deleted"

    model = ScriptedModel(
        [[ModelChunk(tool_call=ToolCall("write")), ModelChunk(tool_call=ToolCall("delete"))], "ok"]
    )
    result = await Agent(model=model, plugins=[Mutating()]).run("Write")
    statuses = {call.name: output.status for call, output in result.tool_calls}
    assert statuses == {"write": ToolStatus.OK, "delete": ToolStatus.OK}


def test_explicit_duplicate_slots_raise_instead_of_silently_overriding():
    with pytest.raises(TypeError, match="Plugin instance"):
        Agent(approvals=Approvals(), plugins=[Approvals(default=Decision.ALLOW)])


@pytest.mark.parametrize("user_answer", [False, True])
async def test_denials_distinguish_policy_from_the_user_in_log_and_result(user_answer):
    class Write(Plugin):
        @tool
        async def write(self) -> str:
            pytest.fail("Denied tool executed")

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("write"))], "blocked"])
    policy = (
        Approvals(default=Decision.ASK, approver=lambda request: Decision.DENY)
        if user_answer
        else Approvals(deny=[Write.write])
    )
    async with Agent(model=model, approvals=policy, plugins=[Write()]).session() as session:
        result = await session.run("Write")
        denied = result.tool_calls[0][1]
        assert denied.denied_by == ("user" if user_answer else "policy")
        assert denied.content == (
            "User declined this action" if user_answer else "Denied by policy"
        )
        applied = session.last(ToolPolicyAppliedEvent)
        assert applied.decision is Decision.DENY
        assert applied.source == ("approval" if user_answer else "policy")


async def test_feature_instruction_state_survives_compaction_without_creating_messages():
    from harnessed.prompts.plugin_instructions import PluginInstructions

    session = SessionLog("context-compaction")
    values = PluginInstructions(session)
    await session.append(TurnStartedEvent("first"))
    await values.set("user", "old")
    await values.set("user", "current")
    await session.append(TurnEndedEvent("first"))
    await session.append(
        CompactionAppliedEvent(len(session), "Earlier work.", before_tokens=0, after_tokens=0)
    )
    text = "\n".join(message.text for message in session.messages)
    assert "current" not in text and "old" not in text
    replay = SessionLog("context-replay", state=session.snapshot())
    assert replay.messages == session.messages
    assert PluginInstructions(replay).values == {"user": "current"}


async def test_public_session_type_and_forks_open_as_independent_conversations():
    from harnessed import Session as LiveSession

    app = Agent(model=ScriptedModel(["first", "alternate", "resumed"]))
    async with app.session() as session:
        assert isinstance(session, LiveSession)
        await session.run("Remember the original")
        branch = session.snapshot()
    async with app.session(state=branch) as alternative:
        await alternative.run("Try another approach")
        assert alternative.messages[-1].text == "alternate"
    async with app.session(state=branch) as resumed:
        assert all(message.text != "Try another approach" for message in resumed.messages)
        await resumed.run("Continue")
        assert resumed.messages[-1].text == "resumed"


async def test_raw_subscription_includes_dependent_teardown_before_it_finishes():
    class Temporary(Plugin):
        state = needs(PluginInstructions)

        async def teardown(self, scope: PluginScope) -> None:
            await self.state.remove("temporary")

    app = Agent(model=ScriptedModel(["done"]), plugins=[Temporary()])
    seen = []
    async with app.session() as session:

        async def observe():
            async for event in session.subscribe():
                seen.append(event)

        observing = asyncio.create_task(observe())
        await asyncio.sleep(0)
        await session.scope[PluginInstructions].set("temporary", "present")
        await session.run("Go")
        assert not observing.done()
    await asyncio.wait_for(observing, 2)
    changes = [event for event in seen if isinstance(event, PluginInstructionsChangedEvent)]
    assert [event.value for event in changes] == ["present", None]
