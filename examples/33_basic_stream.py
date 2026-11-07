import asyncio

from harnessed import Agent, events
from harnessed.models import ModelChunk
from harnessed.testing import ScriptedModel


async def main() -> None:
    model = ScriptedModel([[ModelChunk(text="The patch "), ModelChunk(text="looks good.")]])
    async with Agent(model=model).session() as session:
        async with session.stream("Review the patch.") as run:
            async for event in run:
                if isinstance(event, events.TextDeltaEvent):
                    print(event.text, end="", flush=True)
            result = await run.result()
            print("\nFinished:", result.stop_reason)


if __name__ == "__main__":
    asyncio.run(main())
