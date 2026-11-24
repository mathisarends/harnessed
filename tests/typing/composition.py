from typing import assert_type

from harnessed import Agent, AgentLoop, Plugin
from harnessed.runtime.composition import CompositionDescription, PluginDescription


class OptionalFeature(Plugin):
    pass


class Configured(Plugin):
    pass


optional = [OptionalFeature()]
configured: list[Plugin] = [Configured()]
agent = Agent(
    plugins=[OptionalFeature(), (optional, [configured])],
    loop=AgentLoop(max_steps=6),
)
assert_type(agent.plugins, tuple[Plugin, ...])
assert_type(agent.loop, AgentLoop)
assert_type(agent.describe(), CompositionDescription)
assert_type(agent.describe().plugins, tuple[PluginDescription, ...])
assert_type(agent.describe().plugins[0].slot, str | None)
Agent(plugins=[False])  # ty: ignore[invalid-argument-type]
Agent(plugins=[(None,)])  # ty: ignore[invalid-argument-type]
Agent(plugins=[iter(configured)])  # ty: ignore[invalid-argument-type]
Agent(loop=OptionalFeature())  # ty: ignore[invalid-argument-type]
Agent(loop=AgentLoop())
