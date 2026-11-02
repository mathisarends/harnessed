import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

import harnessed.core.session.store as persistence_store
from harnessed import (
    Agent,
    ApprovalAnswer,
    OutputContract,
    Plugin,
    PluginOrder,
    SessionStatus,
    SystemPrompt,
    ToolCall,
    tool,
)
from harnessed.core.messages import ImageReference, UserMessage
from harnessed.errors import ConfigError, SessionClosed, SessionFormatError, SessionWriteConflict
from harnessed.events import ApprovalRequestedEvent, ContextMessageEvent
from harnessed.integrations.json import JsonSessionStore
from harnessed.plugins import ContextValue, Spill
from harnessed.plugins.spill import SpillStore
from harnessed.sessions import SessionCheckpoint, SessionStore, StoredSession
from harnessed.testing import ScriptedModel, call


class ConversationRepository:
    def __init__(self) -> None:
        self.rows: dict[str, StoredSession] = {}
        self.before_save: Callable[[SessionCheckpoint], None] | None = None

    async def load_session(self, conversation_id: str) -> StoredSession | None:
        return self.rows.get(conversation_id)

    async def save_session(
        self, conversation_id: str, checkpoint: SessionCheckpoint, *, expected_revision: int | None
    ) -> int:
        if self.before_save is not None:
            self.before_save(checkpoint)
        current = self.rows.get(conversation_id)
        revision = current.revision if current is not None else None
        if revision != expected_revision:
            raise SessionWriteConflict(conversation_id)
        revision = 1 if revision is None else revision + 1
        self.rows[conversation_id] = StoredSession(checkpoint, revision)
        return revision


@pytest.fixture(params=["repository", "json"])
def store(request, tmp_path: Path) -> SessionStore:
    return (
        JsonSessionStore(root=tmp_path / "sessions")
        if request.param == "json"
        else ConversationRepository()
    )


async def test_repository_reopens_by_id_and_saves_context_and_results_before_return(store):
    model = ScriptedModel(["first", "second"])
    agent = Agent(model=model, store=store, instructions="Current instructions.")
    async with agent.session(id="tenant:customer/4711") as session:
        await session.context.set("customer", "Ada")
        assert await store.load_session(session.id) is not None
        result = await session.run("Remember this.")
        stored = await store.load_session(session.id)
        assert stored is not None
        assert stored.checkpoint.state.history[-1].message.text == "first"
        assert result.state.context == {"customer": "Ada"}
    async with agent.session(id="tenant:customer/4711") as restored:
        assert restored.context.values == {"customer": "Ada"}
        await restored.run("Continue.")
    assert [message.text for message in model.requests[-1].messages] == [
        "Current instructions.",
        '<context name="customer">Ada</context>',
        "Remember this.",
        "first",
        "Continue.",
    ]


async def test_typed_repository_does_not_require_checkpoint_json_encoding(monkeypatch):
    def forbidden(_self: SessionCheckpoint) -> str:
        raise AssertionError("The runtime must not require JSON for a typed store")

    monkeypatch.setattr(SessionCheckpoint, "to_json", forbidden)
    repository = ConversationRepository()
    agent = Agent(model=ScriptedModel(["done"]), store=repository)
    async with agent.session(id="typed") as session:
        await session.run(UserMessage("Hello", id="application-message"))
    async with agent.session(id="typed") as restored:
        assert restored.messages[0].id == "application-message"
        assert restored.messages[-1].text == "done"


async def test_paused_turn_reopens_without_repeating_tools_and_keeps_limits(store):
    entered = asyncio.Event()
    release = asyncio.Event()
    executions: list[str] = []

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            executions.append("work")
            entered.set()
            await release.wait()
            return "done"

    model = ScriptedModel([call(Work.work), "finished"])
    agent = Agent(model=model, plugins=[Work()], store=store)
    async with agent.session(id="paused") as session:
        handle = await session.submit("Work", max_steps=2)
        await asyncio.wait_for(entered.wait(), timeout=5)
        pausing = asyncio.create_task(session.pause())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(pausing, timeout=5)
        turn = session.log.open_turn
        assert turn is not None and len(turn.steps) == 1
        turn_id = turn.id
    with pytest.raises(SessionClosed):
        await handle.result()
    async with agent.session(id="paused") as restored:
        assert restored.status is SessionStatus.PAUSED
        assert restored.log.open_turn.id == turn_id
        assert restored.log.open_turn.start.limits.max_steps == 2
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert restored.messages[-1].text == "finished"
    assert executions == ["work"]
    assert len(model.requests) == 2


async def test_paused_inbox_and_quiet_context_survive_reopening(store):
    agent = Agent(model=ScriptedModel(["one", "two"]), store=store)
    async with agent.session(id="inbox") as session:
        await session.pause()
    async with agent.session(id="inbox") as session:
        assert session.status is SessionStatus.PAUSED
        await session.context.set("status", "ready")
        await session.submit("First")
        await session.submit("Second")
    async with agent.session(id="inbox") as restored:
        assert restored.status is SessionStatus.PAUSED
        assert restored.context.values == {"status": "ready"}
        assert [item.content.text for item in restored.inbox.pending] == ["First", "Second"]
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert [message.text for message in restored.messages][-4:] == [
            "First",
            "one",
            "Second",
            "two",
        ]


async def test_stale_worker_cannot_overwrite_a_new_revision(store):
    agent = Agent(model=ScriptedModel([]), store=store)
    async with agent.session(id="shared") as first, agent.session(id="shared") as second:
        await first.context.set("owner", "first")
        with pytest.raises(SessionWriteConflict):
            await second.context.set("owner", "second")
    async with agent.session(id="shared") as restored:
        assert restored.context.values == {"owner": "first"}


@pytest.mark.parametrize("mode", ["run", "stream"])
async def test_storage_failure_reaches_the_run_caller_without_returning_success(mode):
    repository = ConversationRepository()

    def fail_terminal(checkpoint: SessionCheckpoint) -> None:
        history = checkpoint.state.history
        if history and history[-1].message.text == "done":
            raise OSError("storage unavailable")

    repository.before_save = fail_terminal
    async with Agent(model=ScriptedModel(["done"]), store=repository).session(
        id="failure"
    ) as session:
        with pytest.raises(OSError, match="storage unavailable"):
            if mode == "run":
                await session.run("Work")
            else:
                async for _event in session.stream("Work"):
                    pass


@pytest.mark.parametrize("operation", ["context", "flush"])
async def test_cancelled_write_finishes_before_cancellation_is_delivered(monkeypatch, operation):
    monkeypatch.setattr(persistence_store, "_CHECKPOINT_INTERVAL", 60.0)
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowRepository(ConversationRepository):
        async def save_session(
            self,
            conversation_id: str,
            checkpoint: SessionCheckpoint,
            *,
            expected_revision: int | None,
        ) -> int:
            entered.set()
            await release.wait()
            return await super().save_session(
                conversation_id, checkpoint, expected_revision=expected_revision
            )

    repository = SlowRepository()
    async with Agent(model=ScriptedModel([]), store=repository).session(id="cancel") as session:
        if operation == "flush":
            await session.emit(ContextMessageEvent(UserMessage("retained")))
            write = asyncio.create_task(session.flush())
        else:
            write = asyncio.create_task(session.context.set("fact", "retained"))
        await asyncio.wait_for(entered.wait(), timeout=5)
        write.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(write, timeout=5)
        assert "retained" in repository.rows["cancel"].checkpoint.to_json()


async def test_restoring_structured_pending_work_requires_the_registered_output_identity(store):
    class Answer(BaseModel):
        value: int

    contract = OutputContract("answer.v1", Answer)
    model = ScriptedModel([call("final_answer", value=7)])
    agent = Agent(model=model, store=store)
    async with agent.session(id="structured") as session:
        await session.pause()
        await session.submit("Answer", output=contract)
    with pytest.raises(ConfigError, match="Restore output contract"):
        async with agent.session(id="structured"):
            pytest.fail("Missing validators must fail before executing stored work")
    async with agent.session(id="structured", restore_outputs=[contract]) as restored:
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert len(model.requests) == 1
        assert model.requests[0].output_schema.id == "answer.v1"


async def test_conversation_id_requires_a_store_and_existing_state_cannot_be_overwritten(store):
    with pytest.raises(ConfigError, match="explicit store"):
        async with Agent(model=ScriptedModel([])).session(id="missing-store"):
            pytest.fail("An ID must not imply persistence")
    agent = Agent(model=ScriptedModel(["done"]), store=store)
    async with agent.session(id="existing") as session:
        state = (await session.run("Hello")).state
    with pytest.raises(ConfigError, match="cannot also receive state"):
        async with agent.session(id="existing", state=state):
            pytest.fail("An explicit state must not overwrite a stored conversation")


async def test_corrupt_checkpoint_does_not_start_an_empty_conversation():
    class CorruptRepository(ConversationRepository):
        async def load_session(self, conversation_id: str) -> StoredSession | None:
            return StoredSession(SessionCheckpoint.from_json('{"version": 99}'), 1)

    repository = CorruptRepository()
    with pytest.raises(SessionFormatError):
        async with Agent(model=ScriptedModel([]), store=repository).session(id="corrupt"):
            pytest.fail("Corruption must fail before the runtime starts")


async def test_approval_grants_survive_repository_reopening(store):
    class Allow:
        async def ask(self, request: ApprovalRequestedEvent) -> ApprovalAnswer:
            return ApprovalAnswer.ALWAYS

    agent = Agent(model=ScriptedModel([]), store=store)
    async with agent.session(id="grants") as session:
        answer = await session.approvals.request(
            ToolCall("work"), "Authorize work", approver=Allow()
        )
        assert answer is ApprovalAnswer.ALWAYS
        grant = session.approvals.grant_for("work", scope="tool-policy")
        assert grant is not None
        request_id = grant.request_id
    async with agent.session(id="grants") as restored:
        grant = restored.approvals.grant_for("work", scope="tool-policy")
        assert grant is not None and grant.request_id == request_id
        await restored.approvals.revoke(request_id)
    async with agent.session(id="grants") as restored:
        assert not restored.approvals.grants


async def test_images_and_spilled_text_survive_a_paused_turn(store):
    entered, release = asyncio.Event(), asyncio.Event()
    text = "large output " * 2000

    class Source(Plugin):
        @tool(readonly=True)
        async def source(self) -> str:
            entered.set()
            await release.wait()
            return text

    model = ScriptedModel([call(Source.source), "finished"])
    agent = Agent(
        model=model,
        store=store,
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
        plugins=[Source(), Spill(threshold_tokens=32)],
    )
    async with agent.session(id="resources") as session:
        image = await session.assets.put(b"pixels\xff\x00", media_type="image/png")
        await session.submit(UserMessage((ImageReference(image),)))
        await asyncio.wait_for(entered.wait(), timeout=5)
        pausing = asyncio.create_task(session.pause())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(pausing, timeout=5)
    async with agent.session(id="resources") as restored:
        assert await restored.assets.read(image) == b"pixels\xff\x00"
        resource = restored.log.resources[0]
        assert await restored.ctx[SpillStore].read(resource.path, limit=16000) == text[:16000]
        await restored.resume()
        await asyncio.wait_for(restored.wait_until_idle(), timeout=5)
        assert restored.messages[-1].text == "finished"


@pytest.mark.parametrize("boundary", ["flush", "close"])
async def test_intermediate_changes_coalesce_and_restore_at_the_requested_boundary(
    monkeypatch, boundary
):
    monkeypatch.setattr(persistence_store, "_CHECKPOINT_INTERVAL", 60.0)
    repository = ConversationRepository()
    agent = Agent(model=ScriptedModel([]), store=repository)
    async with agent.session(id="batch") as session:
        await session.context.set("seed", "ready")
        before = repository.rows["batch"].revision
        await session.emit(ContextMessageEvent(UserMessage("first")))
        await session.emit(ContextMessageEvent(UserMessage("second")))
        assert repository.rows["batch"].revision == before
        if boundary == "flush":
            await session.flush()
            assert repository.rows["batch"].revision == before + 1
    assert repository.rows["batch"].revision == before + 1
    async with agent.session(id="batch") as restored:
        assert [message.text for message in restored.messages][-2:] == ["first", "second"]


async def test_intermediate_changes_are_saved_in_the_background():
    persisted = asyncio.Event()
    repository = ConversationRepository()

    def observe(checkpoint: SessionCheckpoint) -> None:
        history = checkpoint.state.history
        if history and history[-1].message.text == "background fact":
            persisted.set()

    repository.before_save = observe
    async with Agent(model=ScriptedModel([]), store=repository).session(id="background") as session:
        await session.emit(ContextMessageEvent(UserMessage("background fact")))
        await asyncio.wait_for(persisted.wait(), timeout=5)
        stored = repository.rows["background"]
        assert stored.checkpoint.state.history[-1].message.text == ("background fact")


async def test_background_failure_reaches_a_live_handle_and_cleans_up_its_tool():
    entered, cleaned, block = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            entered.set()
            try:
                await block.wait()
                return "done"
            finally:
                cleaned.set()

    repository = ConversationRepository()

    def unavailable(checkpoint: SessionCheckpoint) -> None:
        raise OSError("background storage unavailable")

    agent = Agent(model=ScriptedModel([call(Work.work)]), plugins=[Work()], store=repository)
    async with agent.session(id="failure") as session:
        handle = await session.submit("Work")
        await asyncio.wait_for(entered.wait(), timeout=5)
        repository.before_save = unavailable
        await session.emit(ContextMessageEvent(UserMessage("pending fact")))
        with pytest.raises(OSError, match="background storage unavailable"):
            await asyncio.wait_for(handle.result(), timeout=5)
        await asyncio.wait_for(cleaned.wait(), timeout=5)


async def test_background_failure_while_idle_is_reported_when_closing():
    failed = asyncio.Event()
    repository = ConversationRepository()

    def unavailable(checkpoint: SessionCheckpoint) -> None:
        failed.set()
        raise OSError("idle storage unavailable")

    repository.before_save = unavailable
    agent = Agent(model=ScriptedModel([]), store=repository)
    with pytest.raises(OSError, match="idle storage unavailable"):
        async with agent.session(id="idle") as session:
            await session.emit(ContextMessageEvent(UserMessage("pending fact")))
            await asyncio.wait_for(failed.wait(), timeout=5)


async def test_model_request_is_not_executed_when_its_intent_cannot_be_saved():
    repository = ConversationRepository()
    model = ScriptedModel(["must not execute"])

    def unavailable(checkpoint: SessionCheckpoint) -> None:
        if any(event.type == "model.requested" for event in checkpoint.events):
            raise OSError("model intent unavailable")

    repository.before_save = unavailable
    async with Agent(model=model, store=repository).session(id="intent") as session:
        with pytest.raises(OSError, match="model intent unavailable"):
            await session.run("Work")
    assert not model.requests


async def test_context_provider_can_unmount_while_a_persistent_session_closes(store):
    agent = Agent(model=ScriptedModel(["done"]), store=store, plugins=[ContextValue("ci", "ready")])
    async with agent.session(id="provider") as session:
        result = await session.run("Work")
        assert result.state.context == {"ci": "ready"}
    async with Agent(model=ScriptedModel([]), store=store).session(id="provider") as restored:
        assert "ci" not in restored.context.values


async def test_reported_store_failure_is_not_raised_again_by_context_provider_teardown():
    repository = ConversationRepository()

    def unavailable(checkpoint: SessionCheckpoint) -> None:
        raise OSError("storage unavailable")

    agent = Agent(
        model=ScriptedModel(["done"]), store=repository, plugins=[ContextValue("ci", "ready")]
    )
    async with agent.session(id="provider") as session:
        await session.run("First")
        repository.before_save = unavailable
        with pytest.raises(OSError, match="storage unavailable"):
            await session.run("Second")
