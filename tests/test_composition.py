from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import pytest

from harnessed import (
    Agent,
    Context,
    Inject,
    Instructions,
    Kit,
    Plugin,
    PluginOrder,
    Session,
    SystemPrompt,
    plugin,
    tool,
)
from harnessed.errors import PluginConflict
from harnessed.events import DurableEvent
from harnessed.models import Model
from harnessed.plugins import Filesystem, PromptRenderer, Service, Shell
from harnessed.testing import ScriptedModel, call


class CompositionEventKind(StrEnum):
    CHECKED = "composition.checked"


@dataclass(frozen=True)
class OriginalEvent(DurableEvent):
    type: Literal[CompositionEventKind.CHECKED] = CompositionEventKind.CHECKED


@dataclass(frozen=True)
class ReplacementEvent(DurableEvent):
    type: Literal[CompositionEventKind.CHECKED] = CompositionEventKind.CHECKED


@pytest.mark.parametrize("shell", [False, True])
async def test_explicit_project_composition_adds_only_the_requested_tools(tmp_path, shell):
    (tmp_path / "AGENTS.md").write_text("Use focused changes.", encoding="utf-8")
    model = ScriptedModel(["ready"])
    filesystem = Filesystem(tmp_path)
    commands = [Shell(tmp_path)] if shell else []
    app = Agent(
        model=model,
        prompt=SystemPrompt.from_plugins(
            Instructions.from_file(path=tmp_path / "AGENTS.md"), order=PluginOrder.AS_LISTED
        ),
        plugins=[filesystem, *commands],
    )
    async with app.session() as session:
        await session.run("inspect")
    request = model.requests[0]
    assert request.messages[0].text.startswith("Use focused changes.")
    names = {tool.name for tool in request.tools}
    assert {"read_file", "write_file"} <= names
    assert ("run_command" in names) == shell


async def test_session_installs_waiting_plugins_and_disposes_the_driver_once(monkeypatch):
    starts, closes = [], []
    original_close = Session.aclose

    async def close(self):
        closes.append(self)
        await original_close(self)

    monkeypatch.setattr(Session, "aclose", close)

    @plugin(needs=[Session])
    async def observer(ctx: Context):
        starts.append(ctx[Session])
        yield

    app = Agent(model=ScriptedModel(["ready"]), plugins=[observer()])
    async with app.session() as session:
        assert starts == [session]
        assert (await session.run("hello")).text == "ready"
    assert closes == [session]


async def test_session_service_remains_available_during_dependent_teardown():
    seen = []

    class Dependent(Plugin):
        needs = (Session,)

        async def teardown(self, session: Inject[Session]):
            seen.append(session.id)

    async with Agent(model=ScriptedModel([]), plugins=[Dependent()]).session() as session:
        pass
    assert seen == [session.id]


@pytest.mark.parametrize("remove", [False, True])
async def test_only_survivors_contribute_prompts_events_tools_and_lifetimes(remove):
    started = []

    class Original(Plugin, slot="example.capability"):
        events = (OriginalEvent,)

        def instructions(self) -> SystemPrompt:
            return SystemPrompt(self._usage_instructions())

        def _usage_instructions(self) -> str:
            pytest.fail("Discarded prompt providers must not run")

        @property
        def context_description(self) -> tuple[str, str]:
            pytest.fail("Discarded context metadata must not be collected")

        async def setup(self) -> None:
            pytest.fail("Discarded plugins must not mount")

        @tool
        def answer(self) -> str:
            pytest.fail("Discarded tools must not register")

    class Replacement(Plugin, slot="example.capability"):
        events = (ReplacementEvent,)

        def instructions(self) -> SystemPrompt:
            return SystemPrompt(Instructions(text="Use the selected capability."))

        async def setup(self) -> None:
            started.append(self)

        @tool(readonly=True)
        def answer(self) -> str:
            return "selected"

    kit = Kit("library", [Original()])
    if remove:
        kit = kit.without(Original)
    model = ScriptedModel([call("answer"), "done", call("answer"), "done"])
    agent = Agent(
        model=model,
        plugins=[kit, Replacement()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    assert started == []
    for _ in range(2):
        async with agent.session() as session:
            assert await session.prompt.render() == "Use the selected capability."
            await session.emit(ReplacementEvent())
            with pytest.raises(TypeError, match="registered"):
                await session.emit(OriginalEvent())
            result = await session.run("answer")
            assert result.tool_calls[0][1].text == "selected"
    assert len(started) == 2 and started[0] is not started[1]


def test_replaced_recipe_providers_are_never_evaluated():
    class Feature(Plugin, slot="example.feature"):
        def instructions(self) -> SystemPrompt:
            pytest.fail("Discarded instructions must not be evaluated")

    class Replacement(Feature):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt("Replacement.")

    agent = Agent(
        plugins=[Kit("library", [Feature()]), Replacement()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    assert [entry.section.text for entry in agent.prompt.entries] == ["Replacement."]


async def test_model_and_system_prompt_keywords_override_kit_defaults():
    library_model = ScriptedModel(["library"])
    model = ScriptedModel(["application"])
    agent = Agent(
        model=model,
        prompt=SystemPrompt("Application instructions."),
        plugins=[
            Kit(
                "library",
                [
                    Service(Model, library_model),
                    PromptRenderer(SystemPrompt("Library instructions.")),
                ],
            )
        ],
    )
    result = await agent.run("Go")
    assert result.text == "application" and not library_model.requests
    assert model.requests[0].messages[0].text == "Application instructions."
    rows = {item.slot: item for item in agent.describe().plugins if item.slot is not None}
    assert rows["model"].replaces[0].kit_path == ("library",)
    assert rows["instructions"].replaces[0].kit_path == ("library",)
    with pytest.raises(PluginConflict, match="model"):
        Agent(model=model, plugins=[Service(Model, library_model)])


async def test_instructions_and_models_can_be_supplied_entirely_by_a_kit():
    model = ScriptedModel(["library"])
    result = await Agent(
        plugins=[
            Kit(
                "library",
                [Service(Model, model), PromptRenderer(SystemPrompt("Library instructions."))],
            )
        ]
    ).run("Go")
    assert result.text == "library"
    assert model.requests[0].messages[0].text == "Library instructions."


async def test_explicit_rest_group_follows_the_resolved_slot_positions():
    class LoopGuidance(Plugin, slot="loop"):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt(Instructions(text="Loop guidance."))

    class Feature(Plugin):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt(Instructions(text="Feature guidance."))

    agent = Agent(
        plugins=[Feature(), LoopGuidance()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    assert await agent.prompt.render() == "Loop guidance.\n\nFeature guidance."
