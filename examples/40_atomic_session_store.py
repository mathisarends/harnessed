"""Run with: uv run python -m examples.40_atomic_session_store"""

import asyncio
import sqlite3
from contextlib import closing

from examples._atomic_repository import ConversationRepository, Speaker
from examples._atomic_session_store import ConversationSessionStore
from harnessed import Agent
from harnessed.models import UserMessage
from harnessed.sessions import SessionStore
from harnessed.testing import ScriptedModel


async def main() -> None:
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        repository = ConversationRepository(connection)
        store: SessionStore = ConversationSessionStore(repository)
        model = ScriptedModel(["Remembered.", "Continued."])
        agent = Agent(model=model, store=store)
        async with agent.session(id="support:4711") as session:
            await session.run(UserMessage("Remember Ada.", id="customer-message"))
            messages = await repository.messages(session.id)
            assert [message.speaker for message in messages] == [Speaker.CUSTOMER, Speaker.AGENT]
            assert messages[0].key == "customer-message"
            assert messages[1].text == "Remembered."

        async with agent.session(id="support:4711") as session:
            await session.run("Continue.")
        assert len(await repository.messages("support:4711")) == 4
        print("Messages and recovery checkpoints committed together.")


if __name__ == "__main__":
    asyncio.run(main())
