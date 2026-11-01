from typing import assert_type

from harnessed import (
    Agent,
    Instructions,
    Plugin,
    PluginInstructions,
    PromptFormat,
    SectionDelivery,
    Session,
    SystemPrompt,
    Unplaced,
    instructions,
)
from harnessed.core.prompt.values import InstructionProvider, InstructionRef, PromptPart
from harnessed.kernel import Context


class PromptFeature(Plugin):
    usage = Instructions(title="Usage", text="Use this feature.")

    @instructions(title="Configuration")
    def configuration(self) -> str | None:
        return "Configured."


feature = PromptFeature()
assert_type(PromptFeature.usage, Instructions)
assert_type(feature.usage, InstructionRef)
assert_type(PromptFeature.configuration, InstructionProvider[PromptFeature])
assert_type(feature.configuration, InstructionRef)
assert_type(feature.instruction_refs(), tuple[InstructionRef, ...])
Agent(
    plugins=[feature],
    system_prompt=SystemPrompt(
        feature.configuration, SystemPrompt(feature.usage), format=PromptFormat.MARKDOWN
    ),
)


async def catalog(ctx: Context) -> str:
    return "Catalog."


Instructions.from_factory(factory=catalog, title="Catalog")
Instructions.from_file(path="AGENTS.md", missing_ok=True)
Instructions(text=42)  # ty: ignore[invalid-argument-type]
SystemPrompt("raw text", PluginInstructions(feature), PluginInstructions(), unplaced=Unplaced.ERROR)
SystemPrompt(42)  # ty: ignore[invalid-argument-type]
SystemPrompt(("raw text",))  # ty: ignore[invalid-argument-type]
SystemPrompt(sections=("raw text",))  # ty: ignore[unknown-argument]
parts: list[PromptPart] = ["Role.", feature.usage]
assert_type(SystemPrompt(*parts).sections, tuple[PromptPart, ...])
Agent(system_prompt=42)  # ty: ignore[invalid-argument-type]


async def mount(session: Session) -> None:
    await session.use(feature, section_delivery=SectionDelivery.CONTEXT)
    await session.use(feature, section_delivery=SectionDelivery.IGNORE)
