import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest

from harnessed import Agent, Plugin, SessionStatus, ToolCall, tool
from harnessed.events import (
    AssistantMessageEvent,
    ModelAttemptFinishedEvent,
    ModelAttemptOutcome,
    ModelAttemptStartedEvent,
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelRequestOutcome,
    ModelUsageRecordedEvent,
    TextDeltaEvent,
    ToolCallFinishedEvent,
    ToolExecutionStartedEvent,
    TurnEndedEvent,
    TurnOutcome,
)
from harnessed.hooks import Next, PrepareRequest, StreamModel
from harnessed.kernel import hook
from harnessed.models import (
    GenerationSettings,
    ImageReference,
    ModelAttemptStarted,
    ModelChunk,
    ModelRequest,
    ModelStream,
    ModelUsage,
    UserMessage,
)
from harnessed.testing import CallbackModel, ScriptedModel
from tests._audit import Audit
from tests._images import inline

_COORDINATION_TIMEOUT = 3


async def test_stream_hooks_compose_around_default_with_resolved_input() -> None:
    order: list[str] = []
    seen: list[StreamModel] = []

    class OuterStream(Plugin):
        @hook(PrepareRequest)
        async def prepare(self, event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
            request = await next_()
            return request.append_message(UserMessage("request contribution"))

        @hook(StreamModel, priority=-10)
        async def outer(
            self, event: StreamModel, next_: Next[AbstractAsyncContextManager[ModelStream]]
        ) -> AbstractAsyncContextManager[ModelStream]:
            seen.append(event)
            order.append("outer.hook")
            downstream = await next_()

            @asynccontextmanager
            async def wrap() -> AsyncGenerator[ModelStream, None]:
                order.append("outer.enter")
                try:
                    async with downstream as stream:
                        yield stream
                finally:
                    order.append("outer.exit")

            return wrap()

    class InnerStream(Plugin):
        @hook(StreamModel, priority=10)
        async def inner(
            self, event: StreamModel, next_: Next[AbstractAsyncContextManager[ModelStream]]
        ) -> AbstractAsyncContextManager[ModelStream]:
            seen.append(event)
            order.append("inner.hook")
            downstream = await next_()

            @asynccontextmanager
            async def wrap() -> AsyncGenerator[ModelStream, None]:
                order.append("inner.enter")
                try:
                    async with downstream as stream:
                        yield stream
                finally:
                    order.append("inner.exit")

            return wrap()

    model = ScriptedModel(
        [[ModelChunk(text="answer", reasoning="considered", usage=ModelUsage(7, 2))]],
        settings=GenerationSettings(max_tokens=100, temperature=0.5),
    )
    async with Agent(model=model, plugins=[InnerStream(), OuterStream()]).session() as session:
        result = await session.run(
            UserMessage((inline(b"pixels"),)), settings=GenerationSettings(temperature=0)
        )
        record = session.last(ModelRequestedEvent)
        assert record is not None
        assert len(seen) == 2 and seen[0] is seen[1]
        event = seen[0]
        assert event.session is session.plugin_context
        assert event.model is model
        assert event.request_id == record.request_id
        assert model.requests == [event.request]
        assert event.request.messages[0].content == (inline(b"pixels"),)
        assert isinstance(record.request.messages[0].content[0], ImageReference)
        assert event.request.messages[-1].text == "request contribution"
        assert event.request.settings == GenerationSettings(max_tokens=100, temperature=0)
        assert result.text == "answer" and result.usage == ModelUsage(7, 2)
        message = session.last(AssistantMessageEvent)
        assert message is not None and message.message.reasoning == "considered"
    assert order == [
        "outer.hook",
        "inner.hook",
        "outer.enter",
        "inner.enter",
        "inner.exit",
        "outer.exit",
    ]


async def test_replacement_stream_keeps_tools_usage_and_provider_diagnostics() -> None:
    fallback = ScriptedModel([])
    remote = ScriptedModel(
        [
            [
                ModelChunk(diagnostic=ModelAttemptStarted(attempt=1, max_retries=0)),
                ModelChunk(tool_call=ToolCall("echo", {"value": "remote tool"})),
                ModelChunk(usage=ModelUsage(4, 1)),
            ],
            [
                ModelChunk(diagnostic=ModelAttemptStarted(attempt=1, max_retries=0)),
                ModelChunk(text="remote answer", usage=ModelUsage(8, 2)),
            ],
        ]
    )
    seen: list[StreamModel] = []

    class RemoteTransport(Plugin):
        @hook(StreamModel)
        @asynccontextmanager
        async def tunnel(
            self, event: StreamModel, next_: Next[AbstractAsyncContextManager[ModelStream]]
        ) -> AsyncGenerator[ModelStream, None]:
            seen.append(event)
            async with remote.stream(event.request) as stream:
                yield stream

        @tool(readonly=True)
        async def echo(self, value: str) -> str:
            return value

    async with Agent(model=fallback, plugins=[RemoteTransport()]).session() as session:
        audit = Audit(session)
        async with session.stream("work") as run:
            notifications = [event async for event in run]
        assert not fallback.requests
        assert len(remote.requests) == len(seen) == 2
        assert all(event.model is fallback for event in seen)
        assert len({event.request_id for event in seen}) == 2
        assert [event.text for event in notifications if isinstance(event, TextDeltaEvent)] == [
            "remote answer"
        ]
        started = [event for event in audit.events if isinstance(event, ToolExecutionStartedEvent)]
        finished = [event for event in audit.events if isinstance(event, ToolCallFinishedEvent)]
        assert len(started) == len(finished) == 1
        assert finished[0].result.content == "remote tool"
        assert started[0].call_id == finished[0].call.id
        usage = [event for event in audit.events if isinstance(event, ModelUsageRecordedEvent)]
        assert [event.usage for event in usage] == [ModelUsage(4, 1), ModelUsage(8, 2)]
        assert [event.request_id for event in usage] == [event.request_id for event in seen]
        attempts = [event for event in notifications if isinstance(event, ModelAttemptStartedEvent)]
        finishes = [
            event for event in notifications if isinstance(event, ModelAttemptFinishedEvent)
        ]
        assert [event.request_id for event in attempts] == [event.request_id for event in seen]
        assert [event.outcome for event in finishes] == [
            ModelAttemptOutcome.SUCCEEDED,
            ModelAttemptOutcome.SUCCEEDED,
        ]
        assert all(
            event.model == fallback.info
            for event in audit.events
            if isinstance(event, ModelRequestedEvent)
        )
        assert session.last(TurnEndedEvent).reason is TurnOutcome.COMPLETED


@pytest.mark.parametrize("stage", ["dispatch", "enter"])
async def test_hook_failure_before_stream_entry_closes_request(stage: str) -> None:
    model = ScriptedModel(["recovered"])
    cleanup: list[str] = []

    class BrokenStream(Plugin):
        @hook(StreamModel)
        async def fail(
            self, event: StreamModel, next_: Next[AbstractAsyncContextManager[ModelStream]]
        ) -> AbstractAsyncContextManager[ModelStream]:
            if stage == "dispatch":
                raise ValueError("stream hook failed")

            @asynccontextmanager
            async def open_stream() -> AsyncGenerator[ModelStream, None]:
                try:
                    raise ValueError("stream hook failed")
                    yield  # Entry fails before a stream can be yielded.
                finally:
                    cleanup.append("released")

            return open_stream()

    async with Agent(model=model).session() as session:
        handle = await session.mount(BrokenStream())
        audit = Audit(session)
        with pytest.raises(ValueError, match="stream hook failed"):
            await session.run("fail")
        record = session.last(ModelRequestedEvent)
        assert record is not None and not model.requests
        terminals = [
            event
            for event in audit.events
            if isinstance(event, AssistantMessageEvent | ModelRequestFailedEvent)
        ]
        assert len(terminals) == 1
        assert isinstance(terminals[0], ModelRequestFailedEvent)
        assert terminals[0].outcome is ModelRequestOutcome.FAILED
        assert terminals[0].request_id == record.request_id
        assert session.status is SessionStatus.IDLE
        assert session.last(TurnEndedEvent).reason is TurnOutcome.ABORTED
        assert cleanup == ([] if stage == "dispatch" else ["released"])
        # The failed hook is reversible and does not consume the model's response.
        await handle.aclose()
        assert (await session.run("continue")).text == "recovered"


@pytest.mark.parametrize("stage", ["dispatch", "enter"])
async def test_abort_before_stream_entry_releases_hook_resources(stage: str) -> None:
    ready = asyncio.Event()
    blocked = asyncio.Event()
    cleanup: list[str] = []
    model = ScriptedModel([])

    class WaitingStream(Plugin):
        @hook(StreamModel)
        async def wait(
            self, event: StreamModel, next_: Next[AbstractAsyncContextManager[ModelStream]]
        ) -> AbstractAsyncContextManager[ModelStream]:
            downstream = await next_()
            if stage == "dispatch":
                try:
                    ready.set()
                    await blocked.wait()
                finally:
                    cleanup.append("dispatch")

            @asynccontextmanager
            async def connection() -> AsyncGenerator[ModelStream, None]:
                try:
                    ready.set()
                    await blocked.wait()
                    async with downstream as stream:
                        yield stream
                finally:
                    cleanup.append("enter")

            return connection()

    async with Agent(model=model, plugins=[WaitingStream()]).session() as session:
        audit = Audit(session)
        await session.submit("work")
        await asyncio.wait_for(ready.wait(), timeout=_COORDINATION_TIMEOUT)
        await asyncio.wait_for(session.abort(), timeout=_COORDINATION_TIMEOUT)
        assert cleanup == [stage] and not model.requests
        terminals = [
            event
            for event in audit.events
            if isinstance(event, AssistantMessageEvent | ModelRequestFailedEvent)
        ]
        assert len(terminals) == 1
        assert isinstance(terminals[0], ModelRequestFailedEvent)
        assert terminals[0].outcome is ModelRequestOutcome.CANCELLED
        assert terminals[0].request_id == session.last(ModelRequestedEvent).request_id
        assert session.status is SessionStatus.IDLE


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("partial", [False, True])
async def test_wrapped_stream_releases_resources_and_retains_partial_usage(
    *, cancel: bool, partial: bool
) -> None:
    blocked = asyncio.Event()
    stopped = asyncio.Event()
    cleanup: list[str] = []

    async def chunks(request: ModelRequest) -> AsyncIterator[ModelChunk]:
        try:
            if partial:
                yield ModelChunk(text="visible", usage=ModelUsage(5, 1))
            blocked.set()
            if cancel:
                await stopped.wait()
            raise ValueError("transport failed")
        finally:
            cleanup.append("transport")

    class WrappedStream(Plugin):
        @hook(StreamModel)
        @asynccontextmanager
        async def wrap(
            self, event: StreamModel, next_: Next[AbstractAsyncContextManager[ModelStream]]
        ) -> AsyncGenerator[ModelStream, None]:
            downstream = await next_()
            try:
                async with downstream as stream:
                    yield stream
            finally:
                cleanup.append("wrapper")

    async with Agent(model=CallbackModel(chunks), plugins=[WrappedStream()]).session() as session:
        audit = Audit(session)
        if cancel:
            await session.submit("work")
            await asyncio.wait_for(blocked.wait(), timeout=_COORDINATION_TIMEOUT)
            await session.abort()
        else:
            with pytest.raises(ValueError, match="transport failed"):
                await session.run("work")
        assert cleanup == ["transport", "wrapper"]
        record = session.last(ModelRequestedEvent)
        assert record is not None
        terminals = [
            event
            for event in audit.events
            if isinstance(event, AssistantMessageEvent | ModelRequestFailedEvent)
        ]
        assert len(terminals) == 1 and terminals[0].request_id == record.request_id
        terminal = terminals[0]
        expected_usage = ModelUsage(5, 1) if partial else ModelUsage(0)
        assert session.last(TurnEndedEvent).usage == expected_usage
        if partial:
            assert isinstance(terminal, AssistantMessageEvent)
            assert terminal.interrupted and terminal.message.text == "visible"
            usage = [event for event in audit.events if isinstance(event, ModelUsageRecordedEvent)]
            assert len(usage) == 1 and usage[0].usage == ModelUsage(5, 1)
            assert usage[0].request_id == record.request_id
        else:
            assert isinstance(terminal, ModelRequestFailedEvent)
            assert terminal.outcome is (
                ModelRequestOutcome.CANCELLED if cancel else ModelRequestOutcome.FAILED
            )
            assert not any(isinstance(event, ModelUsageRecordedEvent) for event in audit.events)
        assert session.status is SessionStatus.IDLE


async def test_stream_wrapper_exit_failure_preserves_completed_prefix() -> None:
    cleanup: list[str] = []

    class BrokenExit(Plugin):
        @hook(StreamModel)
        async def exit_failure(
            self, event: StreamModel, next_: Next[AbstractAsyncContextManager[ModelStream]]
        ) -> AbstractAsyncContextManager[ModelStream]:
            downstream = await next_()

            @asynccontextmanager
            async def connection() -> AsyncGenerator[ModelStream, None]:
                async with downstream as stream:
                    yield stream
                cleanup.append("closed")
                raise ValueError("connection close failed")

            return connection()

    model = ScriptedModel([[ModelChunk(text="answer", usage=ModelUsage(3, 2))]])
    async with Agent(model=model, plugins=[BrokenExit()]).session() as session:
        audit = Audit(session)
        with pytest.raises(ValueError, match="connection close failed"):
            await session.run("work")
        assert cleanup == ["closed"]
        messages = [event for event in audit.events if isinstance(event, AssistantMessageEvent)]
        assert len(messages) == 1 and messages[0].interrupted
        assert messages[0].message.text == "answer"
        assert session.last(ModelUsageRecordedEvent).usage == ModelUsage(3, 2)
        assert not any(isinstance(event, ModelRequestFailedEvent) for event in audit.events)
        assert session.last(TurnEndedEvent).reason is TurnOutcome.ABORTED
