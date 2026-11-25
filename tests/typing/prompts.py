from typing import assert_type

from harnessed import Agent, Plugin, Session, SystemPrompt
from harnessed.kernel import PluginScope
from harnessed.prompts import Instructions, PromptFormat, SectionDelivery
from harnessed.prompts.values import PromptPart


class PromptFeature(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt(Instructions(title="Usage", text="Use this feature."), None)


feature = PromptFeature()
assert_type(feature.instructions(), SystemPrompt)
assert_type(feature.with_instructions("Custom usage."), PromptFeature)
assert_type(feature.with_instructions(SystemPrompt()), PromptFeature)
assert_type(feature.without_instructions(), PromptFeature)
feature.with_instructions(42)  # ty: ignore[invalid-argument-type]
layout = SystemPrompt(format=PromptFormat.MARKDOWN)
Agent(instructions=layout, plugins=[feature])
Agent(instructions=layout)
SystemPrompt(42)  # ty: ignore[invalid-argument-type]
SystemPrompt(("raw text",))  # ty: ignore[invalid-argument-type]
SystemPrompt(sections=("raw text",))  # ty: ignore[unknown-argument]
Agent(instructions=42)  # ty: ignore[invalid-argument-type]
Agent(prompt="Role.")  # ty: ignore[unknown-argument]
Agent(system_prompt="Removed")  # ty: ignore[unknown-argument]
parts: list[PromptPart | None] = ["Role.", None, feature.instructions()]
assert_type(SystemPrompt(*parts).sections, tuple[PromptPart, ...])


async def catalog(scope: PluginScope) -> str:
    return "Catalog."


Instructions.from_factory(factory=catalog, title="Catalog")
Instructions.from_file(path="AGENTS.md", missing_ok=True)
Instructions(text=42)  # ty: ignore[invalid-argument-type]


async def mount(session: Session) -> None:
    await session.mount(feature, section_delivery=SectionDelivery.CONTEXT)
