"""Run with: uv run python -m examples.applications.18_atomic_commits"""

import asyncio
import sqlite3
from contextlib import closing

from examples.applications._atomic_commits import ConversationStore
from examples.applications._atomic_repository import ConversationRepository, Speaker
from harnessed import Agent
from harnessed.models import UserMessage
from harnessed.testing import ScriptedModel


async def main() -> None:
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        repository = ConversationRepository(connection)
        store = ConversationStore(repository)
        model = ScriptedModel(["Remembered.", "Continued."])
        agent = Agent(model=model)
        async with agent.session(id="support:4711", store=store) as session:
            await session.run(UserMessage("Remember Ada.", id="customer-message"))
            messages = await repository.messages(session.id)
            assert [message.speaker for message in messages] == [Speaker.CUSTOMER, Speaker.AGENT]
            assert messages[0].key == "customer-message"
            assert messages[1].text == "Remembered."

        async with agent.session(id="support:4711", store=store) as session:
            await session.run("Continue.")
        assert len(await repository.messages("support:4711")) == 4
        print("Messages, journal entries and snapshots committed together.")


if __name__ == "__main__":
    asyncio.run(main())
