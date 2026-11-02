from typing import assert_type

from harnessed import Agent, CompositionDescription, Kit, KitPromptPolicy, Plugin, ResolvedKit
from harnessed.core.composition import PluginDescription, PluginInput, PluginRemoval
from harnessed.kernel import Context, PluginLike, plugin
from harnessed.plugins import AgentLoop


class OptionalFeature(Plugin):
    pass


@plugin
async def configured(ctx: Context):
    yield


kit = Kit("library", [OptionalFeature(), configured, AgentLoop(max_steps=12)])
assert_type(kit.plugins, tuple[PluginInput, ...])
assert_type(kit.without(OptionalFeature), Kit)
resolved = kit.resolve(prompt=KitPromptPolicy.OMIT)
assert_type(resolved, ResolvedKit)
assert_type(resolved.plugins, tuple[PluginLike, ...])
assert_type(resolved.description, CompositionDescription)
Agent(plugins=resolved.plugins)
kit.resolve(prompt=False)  # ty: ignore[invalid-argument-type]
agent = Agent(plugins=[kit, AgentLoop(max_steps=6)])
assert_type(agent.describe(), CompositionDescription)
assert_type(agent.describe().plugins, tuple[PluginDescription, ...])
assert_type(agent.describe().plugins[0].replaces, tuple[PluginDescription, ...])
assert_type(agent.describe().removed, tuple[PluginRemoval, ...])
Kit("invalid", [False])  # ty: ignore[invalid-argument-type]
Kit("optional", [None])
kit.without(OptionalFeature())  # ty: ignore[invalid-argument-type]


async def cannot_mount_a_kit(ctx: Context) -> None:
    await ctx.plugin(kit)  # ty: ignore[invalid-argument-type]
