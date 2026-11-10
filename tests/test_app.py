import asyncio
import gc
import weakref

import pytest

from harnessed import (
    Agent,
    ApprovalAnswer,
    Instructions,
    Plugin,
    PluginConflict,
    SystemPrompt,
    ToolCall,
    tool,
)
from harnessed.core.events.compaction import CompactionAppliedEvent
from harnessed.core.events.messages import PluginInstructionsChangedEvent
from harnessed.core.events.tools import ToolPolicyAppliedEvent
from harnessed.core.events.turn import TurnEndedEvent, TurnStartedEvent
from harnessed.core.messages import PolicyDecision, ToolStatus
from harnessed.core.model import ModelChunk
from harnessed.core.prompt.plugin_instructions import PluginInstructions
from harnessed.core.session.session import SessionLog
from harnessed.kernel import PluginScope, needs
from harnessed.models import Model
from harnessed.plugins import Approvals, PromptRenderer, Service
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
        prompt=SystemPrompt.from_plugins(
            Instructions(text="Be concise."),
            Instructions.from_file(path=path),
        ),
        plugins=[calculator],
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


async def test_facade_parameters_are_equivalent_to_public_plugins():
    one, two = ScriptedModel(["done"]), ScriptedModel(["done"])
    calculator = Calculator()
    await Agent(
        model=one,
        prompt=SystemPrompt.from_plugins(Instructions(text="Rules.")),
        plugins=[calculator],
    ).run("Go")
    calculator_4 = Calculator()
    await Agent(
        plugins=[
            Service(Model, two),
            PromptRenderer(SystemPrompt.from_plugins(Instructions(text="Rules."))),
            calculator_4,
        ]
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


async def test_default_approvals_allow_reads_and_reject_side_effects_without_a_responder():
    class Mutating(Plugin):
        @tool
        async def write(self) -> str:
            pytest.fail("Unapproved side effect ran")

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("write"))], "blocked"])
    result = await Agent(model=model, plugins=[Mutating()]).run("Write")
    assert result.tool_calls[0][1].status == ToolStatus.UNAVAILABLE


def test_explicit_duplicate_slots_raise_instead_of_silently_overriding():
    with pytest.raises(PluginConflict, match="approvals"):
        Agent(plugins=[Approvals(), Approvals(default=PolicyDecision.ALLOW)])


@pytest.mark.parametrize("user_answer", [False, True])
async def test_denials_distinguish_policy_from_the_user_in_log_and_result(user_answer):
    class Write(Plugin):
        @tool
        async def write(self) -> str:
            pytest.fail("Denied tool executed")

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("write"))], "blocked"])
    policy = (
        Approvals(approver=lambda request: ApprovalAnswer.DENY)
        if user_answer
        else Approvals(deny=["write"])
    )
    async with Agent(model=model, plugins=[Write(), policy]).session() as session:
        result = await session.run("Write")
        denied = result.tool_calls[0][1]
        assert denied.denied_by == ("user" if user_answer else "policy")
        assert denied.content == (
            "User declined this action" if user_answer else "Denied by policy"
        )
        applied = session.last(ToolPolicyAppliedEvent)
        assert applied.decision is PolicyDecision.DENY
        assert applied.source == ("approval" if user_answer else "policy")


async def test_feature_instruction_state_survives_compaction_without_creating_messages():
    from harnessed.core.prompt.plugin_instructions import PluginInstructions

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
