"""Run with: uv run python -m examples.12_layered_sdk"""

import asyncio

from harnessed import Agent
from harnessed.kernel import Key, Scope, plugin
from harnessed.plugins import Approvals
from harnessed.plugins.rules import Rules, when
from harnessed.testing import ScriptedModel

backend = Key[str]("backend")


@plugin(needs=[backend])
async def feature(scope: Scope):
    print(f"Independent kernel setup: {scope[backend]}")
    yield
    print("Independent kernel teardown")


async def main():
    async with Scope() as scope:
        handle = await scope.plugin(feature())
        print("Before service:", handle.status)
        remove = scope.provide(backend, "local")
        await scope.settle()
        await remove()
        await scope.settle()
        print("After service removal:", handle.status)
    agent = Agent(
        model=ScriptedModel(["The layered SDK is ready."]),
        plugins=[
            Approvals.allow_all(),
            Rules([when(tool="read_file", result_tokens_over=1000).note("Read a range.")]),
        ],
    )
    async with agent.session() as session:
        print((await session.run("Hello")).text)


if __name__ == "__main__":
    asyncio.run(main())
