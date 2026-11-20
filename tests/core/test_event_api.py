from importlib import import_module
from typing import Annotated, get_args

import pytest
from pydantic import Field, TypeAdapter

from harnessed import Agent, Approvals, Decision, Plugin, events, tool
from harnessed.events import (
    AssistantMessageEvent,
    DurableEvent,
    LogEvent,
    SessionEvent,
    ToolExecutionFinishedEvent,
    TransientEvent,
    TurnEndedEvent,
    stream,
)
from harnessed.events.codec import decode_event, encode_event
from harnessed.events.registry import EventRegistry
from harnessed.events.stream import StreamEvent, TurnResultEvent
from harnessed.kernel import hook
from harnessed.models import ModelChunk, ToolCall
from harnessed.testing import ScriptedModel
from tests._audit import Audit
from tests._events import builtin_event_types, default_tag, tag_from_class_name

type Notification = Annotated[
    events.TextDeltaEvent | events.ReasoningDeltaEvent | events.ApprovalNeededEvent,
    Field(discriminator="type"),
]


@pytest.mark.parametrize("event_type", builtin_event_types())
def test_event_tag_matches_class_name(event_type: type) -> None:
    assert default_tag(event_type) == tag_from_class_name(event_type.__name__)


@pytest.mark.parametrize("event_type", builtin_event_types())
def test_builtin_event_bases_describe_persistence(event_type: type) -> None:
    durable = event_type in get_args(get_args(LogEvent.__value__)[0])
    assert issubclass(event_type, SessionEvent)
    assert issubclass(event_type, DurableEvent) is durable
    assert issubclass(event_type, TransientEvent) is not durable


def test_public_event_exports_cover_every_builtin_with_one_canonical_class_name():
    kinds = builtin_event_types()
    assert kinds
    for kind in kinds:
        assert kind.__name__.endswith("Event")
        namespace = stream if kind is TurnResultEvent else events
        assert getattr(namespace, kind.__name__) is kind
        assert [name for name in vars(namespace) if getattr(namespace, name) is kind] == [
            kind.__name__
        ]


def test_registry_recognizes_every_builtin_stream_and_log_event():
    registry = EventRegistry()
    kinds = builtin_event_types()
    assert kinds
    for kind in kinds:
        assert registry.kind(default_tag(kind)) is kind


@pytest.mark.parametrize(
    "namespace",
    [
        "approval",
        "context",
        "message",
        "reasoning",
        "rule",
        "spill",
        "step",
        "text",
        "tool",
    ],
)
def test_short_name_event_namespaces_are_removed(namespace):
    assert not hasattr(events, namespace)
    with pytest.raises(ModuleNotFoundError):
        import_module(f"harnessed.events.{namespace}")


async def test_direct_public_event_imports_observe_the_canonical_runtime_events():
    observed: list[DurableEvent] = []

    class Observer(Plugin):
        @tool(readonly=True)
        async def echo(self, value: str) -> str:
            return value

        @hook(AssistantMessageEvent)
        async def on_assistant(self, event: AssistantMessageEvent) -> None:
            observed.append(event)

        @hook(ToolExecutionFinishedEvent)
        async def on_tool_finished(self, event: ToolExecutionFinishedEvent) -> None:
            observed.append(event)

        @hook(TurnEndedEvent)
        async def on_turn(self, event: TurnEndedEvent) -> None:
            observed.append(event)

    agent = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("echo", {"value": "hello"}))], "done"]),
        approvals=Approvals(default=Decision.ALLOW),
        plugins=[Observer()],
    )
    async with agent.session() as session:
        session_audit = Audit(session)
        await session.run("Say hello")
        assert [type(event) for event in observed] == [
            AssistantMessageEvent,
            ToolExecutionFinishedEvent,
            AssistantMessageEvent,
            TurnEndedEvent,
        ]
        assert all(any(event is record for record in session_audit.events) for event in observed)


def test_current_record_format_roundtrips_the_turn_end_payload():
    record = {
        "version": 7,
        "type": "turn.ended",
        "data": {
            "turn_id": "turn",
            "reason": "completed",
            "usage": {
                "input_tokens": 15,
                "output_tokens": 4,
                "cache_read_tokens": 10,
                "cache_write_tokens": None,
                "reasoning_tokens": 2,
                "cost": 0.01,
            },
        },
    }
    event = decode_event(record)
    assert type(event) is TurnEndedEvent
    assert encode_event(event) == record


def test_data_notifications_roundtrip_through_a_discriminated_union():
    adapter = TypeAdapter(Notification)
    notifications = [
        events.TextDeltaEvent("message", "hello"),
        events.ReasoningDeltaEvent("message", "thinking"),
        events.ApprovalNeededEvent("request", ToolCall("publish"), "Publish?"),
    ]
    assert set(adapter.json_schema()["discriminator"]["mapping"]) == {
        "text.delta",
        "reasoning.delta",
        "approval.needed",
    }
    for notification in notifications:
        assert adapter.validate_json(adapter.dump_json(notification)) == notification


def test_namespaced_log_events_use_canonical_tags_only():
    event = events.TurnStartedEvent("turn")
    record = encode_event(event)
    assert record["type"] == "turn.started" and record["version"] == 7
    record.update(type="TurnStartedEvent", version=4)
    with pytest.raises(ValueError):
        decode_event(record)
    assert events.CompactionAppliedEvent.type == "compaction.applied"
    assert events.SpillCreatedEvent.type == "spill.created"


def test_live_notifications_cannot_spoof_a_different_registered_discriminator():
    registry = EventRegistry()
    with pytest.raises(TypeError, match="registered"):
        registry.validate(events.TextDeltaEvent("message", "hello", type="reasoning.delta"))


def test_compaction_lifecycle_roundtrips_as_live_events_but_cannot_enter_the_durable_codec():
    adapter = TypeAdapter(StreamEvent[None])
    for event in (
        events.CompactionStartedEvent("compact", before_tokens=100),
        events.CompactionFinishedEvent(
            "compact", events.CompactionOutcome.CANCELLED, before_tokens=100
        ),
    ):
        assert adapter.validate_json(adapter.dump_json(event)) == event
        with pytest.raises(TypeError):
            encode_event(event)
