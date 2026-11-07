import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from examples._atomic_repository import ConversationRepository, Speaker
from examples._atomic_session_store import ConversationSessionStore
from harnessed import Agent
from harnessed.errors import SessionWriteConflict
from harnessed.events import InboxAddedEvent, InboxMode
from harnessed.models import UserMessage
from harnessed.sessions import SessionChange, SessionCommit
from harnessed.testing import ScriptedModel
from tests._checkpoints import checkpoint


async def test_input_and_checkpoint_roll_back_together_before_model_execution():
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        repository = ConversationRepository(connection)
        store = ConversationSessionStore(repository)
        connection.executescript(
            """CREATE TRIGGER reject_checkpoint BEFORE INSERT ON conversations
            BEGIN SELECT RAISE(ABORT, 'checkpoint unavailable'); END;"""
        )
        model = ScriptedModel(["must not run"])
        async with Agent(model=model, store=store).session(id="failed") as session:
            with pytest.raises(sqlite3.IntegrityError, match="checkpoint unavailable"):
                await session.run(UserMessage("Hello", id="customer-message"))
        assert await repository.fetch("failed") is None
        assert await repository.messages("failed") == ()
        assert not model.requests


@pytest.mark.parametrize("mode", ["run", "stream"])
async def test_reply_and_checkpoint_roll_back_without_reporting_success(mode):
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        repository = ConversationRepository(connection)
        store = ConversationSessionStore(repository)
        connection.executescript(
            """CREATE TRIGGER reject_reply BEFORE UPDATE ON conversations
            WHEN EXISTS (SELECT 1 FROM messages WHERE conversation = NEW.key AND speaker = 'agent')
            BEGIN SELECT RAISE(ABORT, 'reply unavailable'); END;"""
        )
        model = ScriptedModel(["done"])
        async with Agent(model=model, store=store).session(id="failed") as session:
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
        stored = await store.load_session("failed")
        assert stored is not None
        assert "done" not in [entry.message.text for entry in stored.checkpoint.state.history]
        assert len(model.requests) == 1


async def test_revision_conflict_rolls_back_staged_domain_rows():
    with closing(sqlite3.connect(":memory:", check_same_thread=False)) as connection:
        repository = ConversationRepository(connection)
        store = ConversationSessionStore(repository)
        assert (
            await store.save_session("shared", SessionCommit(checkpoint()), expected_revision=None)
            == 1
        )
        winner = InboxAddedEvent.create(UserMessage("winner", id="winner"), InboxMode.FOLLOWUP)
        commit = SessionCommit(
            replace(checkpoint(), offset=1, seed=(winner,)), (SessionChange(0, winner),)
        )
        assert await store.save_session("shared", commit, expected_revision=1) == 2
        loser = InboxAddedEvent.create(UserMessage("loser", id="loser"), InboxMode.FOLLOWUP)
        stale = SessionCommit(
            replace(checkpoint(), offset=1, seed=(loser,)), (SessionChange(0, loser),)
        )
        with pytest.raises(SessionWriteConflict):
            await store.save_session("shared", stale, expected_revision=1)
        assert [message.key for message in await repository.messages("shared")] == ["winner"]
        stored = await store.load_session("shared")
        assert stored is not None and stored.revision == 2
        assert stored.checkpoint.seed[0].message.id == "winner"
