import sqlite3
from contextlib import closing

import pytest

from examples.applications._atomic_commits import ConversationCommits
from examples.applications._atomic_repository import ConversationRepository, Speaker
from harnessed import Agent
from harnessed.models import UserMessage
from harnessed.testing import ScriptedModel


async def test_input_and_checkpoint_roll_back_together_before_model_execution():
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        repository = ConversationRepository(connection)
        commits = ConversationCommits(repository)
        connection.executescript(
            """CREATE TRIGGER reject_checkpoint BEFORE INSERT ON journal
            BEGIN SELECT RAISE(ABORT, 'checkpoint unavailable'); END;"""
        )
        model = ScriptedModel(["must not run"])
        async with Agent(model=model).session(id="failed", on_commit=commits) as session:
            with pytest.raises(sqlite3.IntegrityError, match="checkpoint unavailable"):
                await session.run(UserMessage("Hello", id="customer-message"))
        assert await repository.fetch("failed") is None
        assert await repository.messages("failed") == ()
        assert not model.requests


@pytest.mark.parametrize("mode", ["run", "stream"])
async def test_reply_and_checkpoint_roll_back_without_reporting_success(mode):
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        repository = ConversationRepository(connection)
        commits = ConversationCommits(repository)
        connection.executescript(
            """CREATE TRIGGER reject_reply BEFORE INSERT ON journal
            WHEN EXISTS (
                SELECT 1 FROM messages
                WHERE conversation = NEW.conversation AND speaker = 'agent'
            )
            BEGIN SELECT RAISE(ABORT, 'reply unavailable'); END;"""
        )
        model = ScriptedModel(["done"])
        async with Agent(model=model).session(id="failed", on_commit=commits) as session:
            with pytest.raises(sqlite3.IntegrityError, match="reply unavailable"):
                if mode == "run":
                    await session.run(UserMessage("Hello", id="customer-message"))
                else:
                    async with session.stream(
                        UserMessage("Hello", id="customer-message")
                    ) as run_stream:
                        async for _event in run_stream:
                            pass
        messages = await repository.messages("failed")
        assert [(message.speaker, message.text) for message in messages] == [
            (Speaker.CUSTOMER, "Hello")
        ]
        stored = await commits.load("failed")
        assert stored is not None
        assert "done" not in [entry.message.text for entry in stored.state.history]
        assert len(model.requests) == 1


async def test_a_failed_transaction_continues_from_the_last_stored_checkpoint():
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        repository = ConversationRepository(connection)
        commits = ConversationCommits(repository)
        model = ScriptedModel(["first reply", "second reply"])
        agent = Agent(model=model)
        async with agent.session(id="retry", on_commit=commits) as session:
            await session.run(UserMessage("Hello", id="customer-message"))
            connection.executescript(
                """CREATE TRIGGER reject_update BEFORE INSERT ON journal
                BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END;"""
            )
            with pytest.raises(sqlite3.IntegrityError, match="storage unavailable"):
                await session.run(UserMessage("Lost", id="lost-message"))
        connection.execute("DROP TRIGGER reject_update")
        state = await commits.load("retry")
        async with agent.session(state=state, on_commit=commits) as session:
            await session.run(UserMessage("Again", id="second-message"))
        messages = await repository.messages("retry")
        assert [(message.speaker, message.text) for message in messages] == [
            (Speaker.CUSTOMER, "Hello"),
            (Speaker.AGENT, "first reply"),
            (Speaker.CUSTOMER, "Again"),
            (Speaker.AGENT, "second reply"),
        ]
        assert len(model.requests) == 2
