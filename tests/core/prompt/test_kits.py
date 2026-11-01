import pytest

from harnessed import Agent, Kit, Plugin, PluginOrder, PromptFormat, SystemPrompt
from harnessed.errors import PluginConflict


class Files(Plugin, slot="workspace.files"):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt("Files.")


class Browser(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt("Browser.")


class Done(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt("Done.")


async def test_nested_kits_expand_at_list_position_and_keep_completion_last():
    kit = Kit(
        "workspace",
        [Browser(), Kit("files", [Files()]), Done()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    agent = Agent(instructions="Role.", plugins=[kit])
    assert await agent.prompt.render() == "Role.\n\nBrowser.\n\nFiles.\n\nDone."


async def test_removals_and_slot_replacements_are_resolved_before_collection():
    class BetterFiles(Files):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt("Better files.")

    kit = Kit(
        "workspace",
        [Browser(), Kit("nested", [Files()]), Done()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    replaced = Agent(plugins=[BetterFiles(), kit])
    assert await replaced.prompt.render() == "Browser.\n\nBetter files.\n\nDone."
    reduced = Agent(plugins=[kit.without(Files)])
    assert await reduced.prompt.render() == "Browser.\n\nDone."
    assert await Agent(plugins=[kit]).prompt.render() == "Browser.\n\nFiles.\n\nDone."


async def test_parent_and_explicit_agent_prompt_override_nested_defaults():
    inner = Kit(
        "inner",
        [Browser()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED, format=PromptFormat.XML),
    )
    outer = Kit(
        "outer",
        [inner, Files()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED, format=PromptFormat.MARKDOWN),
    )
    assert Agent(plugins=[outer]).prompt.format is PromptFormat.MARKDOWN
    agent = Agent(
        plugins=[outer],
        instructions="Role.",
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED, format=PromptFormat.PLAIN),
    )
    assert await agent.prompt.render() == "Role.\n\nBrowser.\n\nFiles."
    assert agent.describe().plugins[5].slot == "instructions"


def test_sibling_prompt_defaults_require_a_composition_decision():
    one = Kit("one", [Browser()], prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED))
    two = Kit("two", [Files()], prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED))
    with pytest.raises(PluginConflict, match="instructions"):
        Agent(plugins=[one, two])
    agent = Agent(plugins=[one, two], prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED))
    assert [entry.section.text for entry in agent.prompt.entries] == ["Browser.", "Files."]


async def test_optional_plugins_are_normalized_at_both_public_boundaries():
    kit = Kit(
        "workspace",
        [None, Files(), None],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    assert len(kit.plugins) == 1
    assert await Agent(plugins=[None, kit, None]).prompt.render() == "Files."
    assert await Agent(plugins=[kit.without(Files)]).prompt.render() == ""


def test_invalid_kit_prompts_and_removed_fragment_api():
    with pytest.raises(TypeError, match="Kit prompt"):
        Kit("invalid", [], prompt="text")
    with pytest.raises(TypeError):
        Kit("invalid", [], fragments={})
