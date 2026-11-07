from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import pytest

from harnessed import (
    Agent,
    Inject,
    Instructions,
    Plugin,
    PluginOrder,
    PluginPack,
    PluginPackPromptPolicy,
    ResolvedPluginPack,
    Session,
    SystemPrompt,
    plugin,
    tool,
)
from harnessed.errors import PluginConflict
from harnessed.events import DurableEvent
from harnessed.kernel import Scope
from harnessed.models import Model
from harnessed.plugins import AgentLoop, Filesystem, PromptRenderer, Service, Shell
from harnessed.services import SessionContext
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

    @plugin(needs=[SessionContext])
    async def observer(scope: Scope):
        starts.append(scope[SessionContext])
        yield

    app = Agent(model=ScriptedModel(["ready"]), plugins=[observer()])
    async with app.session() as session:
        assert [context.log for context in starts] == [session.log]
        assert (await session.run("hello")).text == "ready"
    assert closes == [session]


async def test_session_service_remains_available_during_dependent_teardown():
    seen = []

    class Dependent(Plugin):
        needs = (SessionContext,)

        async def teardown(self, session: Inject[SessionContext]):
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

    pack = PluginPack("library", [Original()])
    if remove:
        pack = pack.without(Original)
    model = ScriptedModel([call("answer"), "done", call("answer"), "done"])
    agent = Agent(
        model=model,
        plugins=[pack, Replacement()],
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
        plugins=[PluginPack("library", [Feature()]), Replacement()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    assert [entry.section.text for entry in agent.prompt.entries] == ["Replacement."]


async def test_model_and_system_prompt_keywords_override_pack_defaults():
    library_model = ScriptedModel(["library"])
    model = ScriptedModel(["application"])
    agent = Agent(
        model=model,
        prompt=SystemPrompt("Application instructions."),
        plugins=[
            PluginPack(
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
    assert rows["model"].replaces[0].pack_path == ("library",)
    assert rows["instructions"].replaces[0].pack_path == ("library",)
    with pytest.raises(PluginConflict, match="model"):
        Agent(model=model, plugins=[Service(Model, library_model)])


async def test_instructions_and_models_can_be_supplied_entirely_by_a_pack():
    model = ScriptedModel(["library"])
    result = await Agent(
        plugins=[
            PluginPack(
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


@pytest.mark.parametrize("omit", [False, True])
async def test_resolve_keeps_selected_tools_and_instructions_without_mounting(omit):
    mounted = []
    instructed = []

    class Feature(Plugin):
        def instructions(self) -> SystemPrompt:
            instructed.append(True)
            return SystemPrompt("Feature instructions.")

        async def setup(self) -> None:
            mounted.append(True)

        @tool(readonly=True)
        def inspect(self) -> str:
            return "feature result"

    feature = Feature()
    prompt = SystemPrompt.from_plugins("PluginPack strategy.", order=PluginOrder.AS_LISTED)
    pack = PluginPack("library", [feature], prompt=prompt)
    resolved = pack.resolve(prompt=PluginPackPromptPolicy.OMIT) if omit else pack.resolve()
    assert isinstance(resolved, ResolvedPluginPack)
    assert mounted == instructed == []
    assert resolved.plugins[0] is feature
    assert [row.origin for row in resolved.description.plugins] == (
        ["library / Feature"] if omit else ["library / Feature", "library / PromptRenderer"]
    )
    model = ScriptedModel([call(Feature.inspect), "done"])
    agent = Agent(
        model=model,
        plugins=resolved.plugins,
        prompt=(
            SystemPrompt.from_plugins("Application strategy.", order=PluginOrder.AS_LISTED)
            if omit
            else None
        ),
    )
    result = await agent.run("inspect")
    assert result.tool_calls[0][1].text == "feature result"
    assert model.requests[0].messages[0].text == (
        "Application strategy.\n\nFeature instructions."
        if omit
        else "PluginPack strategy.\n\nFeature instructions."
    )
    assert mounted == instructed == [True]
    assert pack.prompt is prompt and pack.plugins == (feature,)


def test_resolve_omits_nested_pack_prompts_before_conflict_resolution():
    first, second = Plugin(name="first"), Plugin(name="second")
    pack = PluginPack(
        "outer",
        [
            PluginPack("first", [first], prompt=SystemPrompt("First strategy.")),
            PluginPack("second", [second], prompt=SystemPrompt("Second strategy.")),
        ],
    )
    with pytest.raises(PluginConflict, match="instructions"):
        pack.resolve()
    resolved = pack.resolve(prompt=PluginPackPromptPolicy.OMIT)
    assert resolved.plugins == (first, second)
    assert [row.origin for row in resolved.description.plugins] == [
        "outer / first / first",
        "outer / second / second",
    ]


async def test_resolve_omit_keeps_explicit_prompt_plugins():
    explicit = PromptRenderer(SystemPrompt("Explicit instructions."))
    pack = PluginPack(
        "outer",
        [PluginPack("child", [explicit], prompt=SystemPrompt("Implicit strategy."))],
    )
    with pytest.raises(PluginConflict, match="instructions"):
        pack.resolve()
    resolved = pack.resolve(prompt=PluginPackPromptPolicy.OMIT)
    assert resolved.plugins == (explicit,)
    agent = Agent(model=ScriptedModel(["done"]), plugins=resolved.plugins)
    result = await agent.run("go")
    assert result.completed
    assert await agent.prompt.render() == "Explicit instructions."


def test_resolve_preserves_order_replacements_and_exclusions():
    original, replacement = AgentLoop(max_steps=12), AgentLoop(max_steps=6)
    before, removed, after = Plugin(name="before"), Plugin(name="removed"), Plugin(name="after")

    class Excluded(Plugin):
        pass

    excluded = Excluded()
    child = PluginPack("child", [before, original, excluded, after]).without(Excluded)
    pack = PluginPack("outer", [child, replacement, removed])
    resolved = pack.resolve(prompt=PluginPackPromptPolicy.OMIT)
    assert resolved.plugins == (before, replacement, after, removed)
    row = resolved.description.plugins[1]
    assert row.origin == "outer / AgentLoop"
    assert row.replaces[0].origin == "outer / child / AgentLoop"
    exclusion = resolved.description.removed[0]
    assert exclusion.plugin.origin == "outer / child / Excluded"
    assert exclusion.removed_by == ("outer", "child")


def test_resolve_rejects_unknown_prompt_policies():
    with pytest.raises(ValueError, match="PluginPackPromptPolicy"):
        PluginPack("empty", []).resolve(prompt="unknown")
