from typing import assert_type

from harnessed import (
    Agent,
    Instructions,
    Plugin,
    PluginOrder,
    PluginPack,
    PromptFormat,
    SectionDelivery,
    Session,
    SystemPrompt,
)
from harnessed.core.prompt.values import PromptPart
from harnessed.kernel import Context


class PromptFeature(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt(Instructions(title="Usage", text="Use this feature."), None)


feature = PromptFeature()
assert_type(feature.instructions(), SystemPrompt)
assert_type(PluginOrder.AS_LISTED, PluginOrder)
layout = SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED, format=PromptFormat.MARKDOWN)
Agent(instructions="Role.", plugins=[None, PluginPack("feature", [feature, None], prompt=layout)])
Agent(prompt=layout)
SystemPrompt.from_plugins()  # ty: ignore[missing-argument]
SystemPrompt.from_plugins(order="as_listed")  # ty: ignore[invalid-argument-type]
SystemPrompt(42)  # ty: ignore[invalid-argument-type]
SystemPrompt(("raw text",))  # ty: ignore[invalid-argument-type]
SystemPrompt(sections=("raw text",))  # ty: ignore[unknown-argument]
Agent(instructions=42)  # ty: ignore[invalid-argument-type]
Agent(prompt="Role.")  # ty: ignore[invalid-argument-type]
Agent(system_prompt="Removed")  # ty: ignore[unknown-argument]
parts: list[PromptPart | None] = ["Role.", None, feature.instructions()]
assert_type(SystemPrompt(*parts).sections, tuple[PromptPart, ...])


async def catalog(ctx: Context) -> str:
    return "Catalog."


Instructions.from_factory(factory=catalog, title="Catalog")
Instructions.from_file(path="AGENTS.md", missing_ok=True)
Instructions(text=42)  # ty: ignore[invalid-argument-type]


async def mount(session: Session) -> None:
    await session.use(feature, section_delivery=SectionDelivery.CONTEXT)
