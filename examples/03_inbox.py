"""Run with: uv run python -m examples.03_inbox"""

import asyncio

from harnessed import Agent, Role
from harnessed.models import ModelChunk
from harnessed.plugins import Approvals
from harnessed.testing import CallbackModel


class PausingModel(CallbackModel):
    def __init__(self):
        super().__init__(self._chunks)
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()

    async def _chunks(self, request):
        if len(self.requests) == 1:
            self.started.set()
            await self.proceed.wait()
        users = [m.content for m in request.messages if m.role == Role.USER]
        yield ModelChunk(text=f"Step {len(self.requests)}: {users[-1]}")


async def main():
    model = PausingModel()
    agent = Agent(model=model, plugins=[Approvals.allow_all()])
    async with agent.session() as session:
        await session.add_context("This project uses Python 3.12+.")
        print(f"After a quiet context update: {session.status}, {len(model.requests)} requests")
        first = await session.submit("Analyze the project.")
        await model.started.wait()
        await session.steer("Focus on the tests.")
        await session.add_context("Unit tests already exist.")
        second = await session.submit("Write a summary afterward.")
        model.proceed.set()
        results = (await first.result(), await second.result())
        await session.wait_until_idle()
        assert results[0].turn_id != results[1].turn_id
        for index, request in enumerate(model.requests, 1):
            print(f"\nRequest {index}:")
            for message in request.messages:
                if message.role in (Role.USER, Role.SYSTEM):
                    print(f"  {message.text}")
        print(f"\n{len(results)} turns, {len(model.requests)} steps, status: {session.status}")
        for number, result in enumerate(results, 1):
            print(f"Turn {number}: {result.turn_id}, reason: {result.stop_reason}")


if __name__ == "__main__":
    asyncio.run(main())
