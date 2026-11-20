from typing import assert_type

from harnessed import Agent, AgentLoop, CompositionDescription, Plugin
from harnessed.kernel import PluginLike, Scope, plugin
from harnessed.runtime.composition import PluginDescription


class OptionalFeature(Plugin):
    pass


@plugin
async def configured(scope: Scope):
    yield


enabled = False
agent = Agent(
    plugins=[OptionalFeature(), configured, OptionalFeature() if enabled else None],
    loop=AgentLoop(max_steps=6),
)
assert_type(agent.plugins, tuple[PluginLike, ...])
assert_type(agent.loop, AgentLoop)
assert_type(agent.describe(), CompositionDescription)
assert_type(agent.describe().plugins, tuple[PluginDescription, ...])
assert_type(agent.describe().plugins[0].slot, str | None)
Agent(plugins=[False])  # ty: ignore[invalid-argument-type]
Agent(loop=OptionalFeature())  # ty: ignore[invalid-argument-type]
Agent(loop=AgentLoop())
