from typing import assert_type

from harnessed import Agent, OutputContract, SessionState
from harnessed.events import ControlState
from harnessed.integrations.json import JsonSessionStore
from harnessed.sessions import (
    CheckpointEntry,
    CheckpointSeed,
    SessionCheckpoint,
    SessionStore,
    StoredSession,
)


async def repository_consumer(store: SessionStore, checkpoint: SessionCheckpoint) -> None:
    stored = await store.load_session("conversation")
    assert_type(stored, StoredSession | None)
    if stored is not None:
        assert_type(stored.checkpoint, SessionCheckpoint)
    assert_type(checkpoint.state, SessionState)
    assert_type(checkpoint.control.state, ControlState)
    assert_type(checkpoint.events, tuple[CheckpointEntry, ...])
    assert_type(checkpoint.seed, tuple[CheckpointSeed, ...])
    revision = await store.save_session("conversation", checkpoint, expected_revision=None)
    assert_type(revision, int)
    agent = Agent(store=store)
    async with agent.session(id="conversation", restore_outputs=[OutputContract("answer.v1", int)]):
        pass
    local: SessionStore = JsonSessionStore(root=".harnessed/sessions")
    assert_type(await local.load_session("conversation"), StoredSession | None)
