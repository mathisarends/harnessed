import json
from dataclasses import FrozenInstanceError, dataclass, is_dataclass, replace
from typing import ClassVar

import pytest

from harnessed import Agent as Application
from harnessed import Approvals, Decision, Plugin, tool
from harnessed.events import DurableEvent, ModelRequestedEvent, SystemMessageEvent, TurnEndedEvent
from harnessed.events.codec import decode_event, encode_event
from harnessed.events.unions import SESSION_EVENT_TYPES
from harnessed.kernel import hook
from harnessed.models import ModelChunk, SystemMessage, ToolCall, ToolResult
from harnessed.runtime.hooks import PrepareRequest
from harnessed.sessions.log import SessionLog, derive_messages, derive_requests
from harnessed.testing import ScriptedModel
from tests._audit import Audit, AuditProjection


def roundtrip(events):
    return tuple(
        decode_event(record) for record in json.loads(json.dumps([encode_event(e) for e in events]))
    )


async def test_every_log_prefix_reconstructs_the_same_history_and_requests():

    class FeaturesPlugin(Plugin):
        @tool
        async def add(self, a: int, b: int) -> int:
            return a + b

    features = FeaturesPlugin(name="math")

    model = ScriptedModel(
        [
            [ModelChunk(text="checking"), ModelChunk(tool_call=ToolCall("add", {"a": 1, "b": 2}))],
            [ModelChunk(reasoning="done"), ModelChunk(text="3")],
        ]
    )
    async with Application(
        model=model, approvals=Approvals(default=Decision.ALLOW), plugins=[features]
    ).session() as agent:
        agent_audit = Audit(agent)
        await agent.run("Add. Ticket: one")
        log = agent_audit.events
        restored = roundtrip(log)
        for offset in range(len(log) + 1):
            prefix = log[:offset]
            replay = AuditProjection(restored[:offset])
            assert replay.messages == derive_messages(prefix) == derive_messages(iter(prefix))
            assert replay.requests == derive_requests(prefix) == derive_requests(iter(prefix))
            assert replay.turns == AuditProjection(prefix).turns
        for seq, event in enumerate(log):
            if isinstance(event, ModelRequestedEvent):
                assert event.request.messages == derive_messages(log[:seq])
        assert tuple(model.requests) == agent_audit.requests


async def test_exact_requests_include_request_hook_changes_without_rerunning_the_hook():
    model = ScriptedModel(["done"])
    harness = Application(model=model, approvals=Approvals(default=Decision.ALLOW))
    invocations = []

    async def configure(event, next_):
        invocations.append(True)
        request = await next_()
        return replace(request, messages=(*request.messages, SystemMessage("request context")))

    async with harness.session() as agent:
        agent.scope.on(PrepareRequest, configure)
        agent_audit = Audit(agent)
        await agent.run("hello")
        replay = AuditProjection(roundtrip(agent_audit.events))
        assert replay.requests == tuple(model.requests)
        assert replay.requests[0].messages[-1].text == "request context"
        assert invocations == [True]


@dataclass(frozen=True)
class UnknownEvent(DurableEvent):
    type: ClassVar[str] = "unknown/event"


@pytest.mark.parametrize("event", [DurableEvent(), UnknownEvent(), SystemMessageEvent(["mutable"])])
async def test_memory_logs_seeds_and_codecs_reject_unregistered_or_mutable_events(event):
    session = SessionLog("immutable")
    with pytest.raises(TypeError):
        await session.append(event)
    with pytest.raises(TypeError):
        AuditProjection([event])
    with pytest.raises(TypeError):
        encode_event(event)
    assert session.events == ()


def test_tool_results_reject_unsupported_parts_before_reaching_the_log():
    with pytest.raises(TypeError, match="text/image parts"):
        ToolResult(["mutable"])


def test_durable_event_classes_are_frozen_and_event_snapshots_cannot_be_changed():
    assert all(
        is_dataclass(kind) and kind.__dataclass_params__.frozen
        for kind in SESSION_EVENT_TYPES.values()
    )
    event = SystemMessageEvent("hello")
    with pytest.raises(FrozenInstanceError):
        event.content = "changed"
    with pytest.raises(ValueError):
        TurnEndedEvent("turn", "invalid")


async def test_plugins_hook_committed_durable_events_directly():
    seen: list[DurableEvent] = []
    turns: list[TurnEndedEvent] = []

    class Recorder(Plugin):
        def __init__(self, events: list[DurableEvent], ended: list[TurnEndedEvent]) -> None:
            super().__init__()
            self.events = events
            self.ended = ended

        @hook(DurableEvent)
        def committed(self, event: DurableEvent) -> None:
            self.events.append(event)

        @hook(TurnEndedEvent)
        def turn_ended(self, event: TurnEndedEvent) -> None:
            self.ended.append(event)

    agent = Application(model=ScriptedModel(["hello"]), plugins=[Recorder(seen, turns)])
    async with agent.session() as session:
        audit = Audit(session)
        result = await session.run("Hi")
    assert seen[-len(audit.events) :] == list(audit.events)
    assert [event.turn_id for event in turns] == [result.turn_id]
