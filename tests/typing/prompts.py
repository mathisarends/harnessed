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
from harnessed.core.prompt.values import InstructionProvider, InstructionRef
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
        format=PromptFormat.MARKDOWN,
        sections=(feature.configuration, SystemPrompt(sections=(feature.usage,))),
    ),
)


async def catalog(ctx: Context) -> str:
    return "Catalog."


Instructions.from_factory(factory=catalog, title="Catalog")
Instructions.from_file(path="AGENTS.md", missing_ok=True)
Instructions(text=42)  # ty: ignore[invalid-argument-type]
SystemPrompt(
    sections=("raw text", PluginInstructions(feature), PluginInstructions()),
    unplaced=Unplaced.ERROR,
)
SystemPrompt(sections=(42,))  # ty: ignore[invalid-argument-type]
Agent(system_prompt=42)  # ty: ignore[invalid-argument-type]


async def mount(session: Session) -> None:
    await session.use(feature, section_delivery=SectionDelivery.CONTEXT)
    await session.use(feature, section_delivery=SectionDelivery.IGNORE)
