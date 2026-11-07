"""Run with: uv run python -m examples.38_session_store"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from harnessed import Agent
from harnessed.integrations.json import JsonSessionStore
from harnessed.models import UserMessage
from harnessed.testing import ScriptedModel


async def main() -> None:
    with TemporaryDirectory(prefix="harnessed-sessions-") as directory:
        store = JsonSessionStore(root=Path(directory))
        agent = Agent(model=ScriptedModel(["Remembered.", "Continued."]), store=store)
        async with agent.session(id="support:4711") as session:
            await session.append_message(UserMessage("The customer is Ada."))
            first = await session.run("Remember the customer.")
            print(first.text)

        async with agent.session(id="support:4711") as restored:
            assert restored.messages[0].text == "The customer is Ada."
            second = await restored.run("Continue our conversation.")
            print(second.text)

        async with agent.session(id="queued") as session:
            await session.pause()
            await session.submit("Wait for another request.")

        next_request = Agent(model=ScriptedModel(["Resumed."]), store=store)
        async with next_request.session(id="queued") as restored:
            await restored.resume()
            await restored.wait_until_idle()
            print(restored.messages[-1].text)


if __name__ == "__main__":
    asyncio.run(main())
