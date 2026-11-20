"""Run with: uv run python -m examples.12_layered_sdk"""

import asyncio
from typing import override

from harnessed import Agent, Plugin
from harnessed.kernel import Key, PluginScope, Scope
from harnessed.plugins.rules import Rules, when
from harnessed.testing import ScriptedModel

backend = Key[str]("backend")


class Feature(Plugin, needs=[backend]):
    async def setup(self, scope: PluginScope) -> None:
        print(f"Independent kernel setup: {scope[backend]}")

    @override
    async def teardown(self, scope: PluginScope) -> None:
        print("Independent kernel teardown")


async def main():
    async with Scope() as scope:
        handle = await scope.plugin(Feature())
        print("Before service:", handle.status)
        remove = scope.provide(backend, "local")
        await scope.settle()
        await remove()
        await scope.settle()
        print("After service removal:", handle.status)
    agent = Agent(
        model=ScriptedModel(["The layered SDK is ready."]),
        plugins=[Rules([when(tool="read_file", result_tokens_over=1000).note("Read a range.")])],
    )
    async with agent.session() as session:
        print((await session.run("Hello")).text)


if __name__ == "__main__":
    asyncio.run(main())
