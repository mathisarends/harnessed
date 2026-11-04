from typing import assert_type

from harnessed import (
    Agent,
    CompositionDescription,
    Plugin,
    PluginPack,
    PluginPackPromptPolicy,
    ResolvedPluginPack,
)
from harnessed.core.composition import PluginDescription, PluginInput, PluginRemoval
from harnessed.kernel import Context, PluginLike, plugin
from harnessed.plugins import AgentLoop


class OptionalFeature(Plugin):
    pass


@plugin
async def configured(ctx: Context):
    yield


pack = PluginPack("library", [OptionalFeature(), configured, AgentLoop(max_steps=12)])
assert_type(pack.plugins, tuple[PluginInput, ...])
assert_type(pack.without(OptionalFeature), PluginPack)
resolved = pack.resolve(prompt=PluginPackPromptPolicy.OMIT)
assert_type(resolved, ResolvedPluginPack)
assert_type(resolved.plugins, tuple[PluginLike, ...])
assert_type(resolved.description, CompositionDescription)
Agent(plugins=resolved.plugins)
pack.resolve(prompt=False)  # ty: ignore[invalid-argument-type]
agent = Agent(plugins=[pack, AgentLoop(max_steps=6)])
assert_type(agent.describe(), CompositionDescription)
assert_type(agent.describe().plugins, tuple[PluginDescription, ...])
assert_type(agent.describe().plugins[0].replaces, tuple[PluginDescription, ...])
assert_type(agent.describe().removed, tuple[PluginRemoval, ...])
PluginPack("invalid", [False])  # ty: ignore[invalid-argument-type]
PluginPack("optional", [None])
pack.without(OptionalFeature())  # ty: ignore[invalid-argument-type]


async def cannot_mount_a_pack(ctx: Context) -> None:
    await ctx.plugin(pack)  # ty: ignore[invalid-argument-type]
