import pytest

from harnessed import Agent, Instructions, Kit, Plugin, SystemPrompt, instructions
from harnessed.errors import ConfigError


class Files(Plugin, slot="workspace.files"):
    usage = Instructions(text="Files.")


class Browser(Plugin):
    usage = Instructions(text="Browser.")


async def test_named_fragment_order_nested_kits_removal_and_slot_replacement():
    files, browser = Files(), Browser()
    nested = Kit("browser", (browser,))
    workspace = Kit(
        "workspace", (files, nested), fragments={"instructions": (nested, files), "files": (files,)}
    )
    assert (
        await Agent(
            plugins=(workspace,), system_prompt=SystemPrompt(sections=(workspace.instructions,))
        ).prompt.render()
        == "Browser.\n\nFiles."
    )

    class BetterFiles(Files):
        usage = Instructions(text="Better files.")

    agent = Agent(
        plugins=(workspace, BetterFiles()),
        system_prompt=SystemPrompt(sections=("Role.", workspace.instructions, "Response.")),
    )
    assert await agent.prompt.render() == "Role.\n\nBrowser.\n\nBetter files.\n\nResponse."
    reduced = workspace.without(Files)
    assert (
        await Agent(
            plugins=(reduced,), system_prompt=SystemPrompt(sections=(reduced.instructions,))
        ).prompt.render()
        == "Browser."
    )


async def test_individual_reference_detects_new_declarations_but_fragment_accepts_them():
    class Extended(Files):
        extra = Instructions(text="New guidance.")

    files = Extended()
    kit = Kit("workspace", (files,))
    with pytest.raises(ConfigError, match="Extended.extra"):
        Agent(plugins=(kit,), system_prompt=SystemPrompt(sections=(files.usage,)))
    assert (
        await Agent(
            plugins=(kit,), system_prompt=SystemPrompt(sections=(kit.instructions,))
        ).prompt.render()
        == "Files.\n\nNew guidance."
    )


async def test_fragment_exclusion_skips_all_providers_and_empty_fragments_remain_valid():
    class Feature(Plugin):
        @instructions()
        def usage(self) -> str:
            pytest.fail("Excluded fragment providers must not run")

    kit = Kit("workspace", (Feature(),))
    assert (
        await Agent(
            plugins=(kit,),
            system_prompt=SystemPrompt(sections=("Role.",), exclude=(kit.instructions,)),
        ).prompt.render()
        == "Role."
    )
    empty = kit.without(Feature)
    assert (
        await Agent(
            plugins=(empty,), system_prompt=SystemPrompt(sections=(empty.instructions,))
        ).prompt.render()
        == ""
    )


def test_foreign_and_overlapping_fragments_fail_before_providers_run():
    class Feature(Plugin):
        @instructions()
        def usage(self) -> str:
            pytest.fail("Invalid placements must fail before providers run")

    feature = Feature()
    kit = Kit("workspace", (feature,), fragments={"usage": (feature,)})
    foreign = Kit("foreign", ())
    with pytest.raises(ConfigError, match="uninstalled kit"):
        Agent(plugins=(kit,), system_prompt=SystemPrompt(sections=(foreign.instructions,)))
    with pytest.raises(ConfigError, match="twice"):
        Agent(
            plugins=(kit,),
            system_prompt=SystemPrompt(sections=(kit.instructions, kit.fragment("usage"))),
        )
    with pytest.raises(ValueError, match="outside this kit"):
        Kit("invalid", (), fragments={"usage": (feature,)})
    with pytest.raises(KeyError, match="no fragment"):
        kit.fragment("missing")


def test_empty_rendering_still_requires_placement_and_does_not_use_class_docstrings():
    class Feature(Plugin):
        """This documentation must not become model instructions."""

        usage = Instructions(text="")

    feature = Feature()
    with pytest.raises(ConfigError, match="Feature.usage"):
        Agent(plugins=(feature,))
    agent = Agent(plugins=(feature,), system_prompt=SystemPrompt(sections=(feature.usage,)))
    assert len(agent.prompt.entries) == 1 and agent.prompt.entries[0].section.text == ""
