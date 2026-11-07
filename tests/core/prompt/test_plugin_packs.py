import pytest

from harnessed import Agent, Plugin, PluginPack, PromptFormat, SystemPrompt
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


async def test_nested_packs_expand_at_list_position_and_keep_completion_last():
    pack = PluginPack(
        "workspace",
        [Browser(), PluginPack("files", [Files()]), Done()],
        prompt=SystemPrompt.from_plugins(),
    )
    agent = Agent(instructions="Role.", plugins=[pack])
    assert await agent.prompt.render() == "Role.\n\nBrowser.\n\nFiles.\n\nDone."


async def test_removals_and_slot_replacements_are_resolved_before_collection():
    class BetterFiles(Files):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt("Better files.")

    pack = PluginPack(
        "workspace",
        [Browser(), PluginPack("nested", [Files()]), Done()],
        prompt=SystemPrompt.from_plugins(),
    )
    replaced = Agent(plugins=[BetterFiles(), pack])
    assert await replaced.prompt.render() == "Browser.\n\nBetter files.\n\nDone."
    reduced = Agent(plugins=[pack.without(Files)])
    assert await reduced.prompt.render() == "Browser.\n\nDone."
    assert await Agent(plugins=[pack]).prompt.render() == "Browser.\n\nFiles.\n\nDone."


async def test_parent_and_explicit_agent_prompt_override_nested_defaults():
    inner = PluginPack(
        "inner",
        [Browser()],
        prompt=SystemPrompt.from_plugins(format=PromptFormat.XML),
    )
    outer = PluginPack(
        "outer",
        [inner, Files()],
        prompt=SystemPrompt.from_plugins(format=PromptFormat.MARKDOWN),
    )
    assert Agent(plugins=[outer]).prompt.format is PromptFormat.MARKDOWN
    agent = Agent(
        plugins=[outer],
        instructions="Role.",
        prompt=SystemPrompt.from_plugins(format=PromptFormat.PLAIN),
    )
    assert await agent.prompt.render() == "Role.\n\nBrowser.\n\nFiles."
    assert agent.describe().plugins[5].slot == "instructions"


def test_sibling_prompt_defaults_require_a_composition_decision():
    one = PluginPack("one", [Browser()], prompt=SystemPrompt.from_plugins())
    two = PluginPack("two", [Files()], prompt=SystemPrompt.from_plugins())
    with pytest.raises(PluginConflict, match="instructions"):
        Agent(plugins=[one, two])
    agent = Agent(plugins=[one, two], prompt=SystemPrompt.from_plugins())
    assert [entry.section.text for entry in agent.prompt.entries] == ["Browser.", "Files."]


async def test_optional_plugins_are_normalized_at_both_public_boundaries():
    pack = PluginPack(
        "workspace",
        [None, Files(), None],
        prompt=SystemPrompt.from_plugins(),
    )
    assert len(pack.plugins) == 1
    assert await Agent(plugins=[None, pack, None]).prompt.render() == "Files."
    assert await Agent(plugins=[pack.without(Files)]).prompt.render() == ""


def test_invalid_pack_prompts_and_removed_fragment_api():
    with pytest.raises(TypeError, match="PluginPack prompt"):
        PluginPack("invalid", [], prompt="text")
    with pytest.raises(TypeError):
        PluginPack("invalid", [], fragments={})
