from typing import assert_type

from harnessed import Agent, OutputContract
from harnessed.integrations.json import JsonSessionStore
from harnessed.sessions import SessionStore, StoredSession


async def repository_consumer(store: SessionStore) -> None:
    stored = await store.load_session("conversation")
    assert_type(stored, StoredSession | None)
    revision = await store.save_session("conversation", "{}", expected_revision=None)
    assert_type(revision, int)
    agent = Agent(store=store)
    async with agent.session(id="conversation", restore_outputs=[OutputContract("answer.v1", int)]):
        pass
    local: SessionStore = JsonSessionStore(root=".harnessed/sessions")
    assert_type(await local.load_session("conversation"), StoredSession | None)
