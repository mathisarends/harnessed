import asyncio
import inspect
import json
import os
import subprocess
import sys
from collections.abc import Generator
from contextlib import nullcontext
from dataclasses import dataclass, replace
from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread, get_ident

import pytest
from pydantic import BaseModel, JsonValue, SecretStr

from harnessed import PolicyDecision
from harnessed.models import ProviderState

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harnessed import Agent, ApprovalAnswer, Plugin, ToolCall, tool
from harnessed.core.events.session import ControlState, SessionControlChangedEvent
from harnessed.core.hooks import SessionAppended, SessionClosing
from harnessed.core.messages import AssistantMessage, ToolResult, ToolStatus, UserMessage
from harnessed.core.model import (
    GenerationSettings,
    ModelChunk,
    ModelRequest,
    ModelUsage,
    StopReason,
)
from harnessed.core.tracing import Tracer
from harnessed.integrations.opentelemetry import (
    ContentCapture,
    OpenTelemetryTracer,
    TraceContent,
    TraceContentKind,
    otlp_provider,
)
from harnessed.kernel import Next, Waterfall, hook
from harnessed.plugins import Approvals, Compaction, every_turns
from harnessed.testing import CallbackModel, ScriptedModel, reply
from harnessed.tracing import record, traced
from harnessed.tracing import span as library_span

type Collector = tuple[str, list[tuple[str, HTTPMessage, bytes]]]


@pytest.fixture
def otlp_collector() -> Generator[Collector]:
    requests: list[tuple[str, HTTPMessage, bytes]] = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(
                (self.path, self.headers, self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


@pytest.fixture
def shutdown_threads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    threads: list[int] = []
    original_shutdown = TracerProvider.shutdown

    def shutdown(provider: TracerProvider) -> None:
        threads.append(get_ident())
        original_shutdown(provider)

    monkeypatch.setattr(TracerProvider, "shutdown", shutdown)
    return threads


@pytest.fixture
def telemetry():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider, exporter
    provider.shutdown()


async def test_runtime_tree_usage_and_backend_attributes(telemetry):
    provider, exporter = telemetry
    usage = ModelUsage(20, 7, cache_read_tokens=4, reasoning_tokens=2, cost=0.01)
    model = ScriptedModel([reply("answer", usage=usage)])
    parent_tracer = provider.get_tracer("backend")
    with parent_tracer.start_as_current_span("http.request") as parent:
        await Agent(model=model, plugins=[OpenTelemetryTracer(tracer_provider=provider)]).run(
            "secret prompt"
        )
    spans = {span.name: span for span in exporter.get_finished_spans()}
    turn, step, model = [spans[f"harnessed.{name}"] for name in ("turn", "step", "model")]
    assert turn.parent.span_id == parent.get_span_context().span_id
    assert step.parent.span_id == turn.context.span_id
    assert model.parent.span_id == step.context.span_id
    assert {span.context.trace_id for span in spans.values()} == {turn.context.trace_id}
    assert model.attributes["openinference.span.kind"] == "LLM"
    assert model.attributes["langfuse.observation.type"] == "generation"
    assert model.attributes["llm.model_name"] == "scripted"
    assert model.attributes["llm.token_count.prompt"] == 20
    assert model.attributes["llm.token_count.completion"] == 7
    assert model.attributes["llm.token_count.total"] == 27
    assert model.attributes["gen_ai.usage.cost"] == 0.01
    assert model.attributes["harnessed.request_id"]
    assert turn.attributes["harnessed.outcome"] == "completed"
    for span in (turn, step, model):
        assert span.attributes["session.id"] == turn.attributes["session.id"]
        assert span.attributes["harnessed.turn_id"] == turn.attributes["harnessed.turn_id"]
        assert "input.value" not in span.attributes
        assert "output.value" not in span.attributes
    assert "secret prompt" not in str([span.attributes for span in spans.values()])
    assert trace.get_current_span().get_span_context().is_valid is False


async def test_turn_summaries_are_redacted_and_scoped_to_each_turn(telemetry):
    provider, exporter = telemetry
    tracer = OpenTelemetryTracer(
        tracer_provider=provider, capture_content=ContentCapture.redacted("credential")
    )
    async with Agent(
        model=ScriptedModel(["first credential answer", "second answer"]),
        instructions="system instructions stay out of the turn summary",
        plugins=[tracer],
    ).session() as session:
        first = await session.run("first credential question")
        second = await session.run("second question")
    turns = [item for item in exporter.get_finished_spans() if item.name == "harnessed.turn"]
    assert len(turns) == 2
    assert first.text == "first credential answer" and second.text == "second answer"
    assert json.loads(turns[0].attributes["input.value"]) == [
        {"role": "user", "content": "first [redacted] question"}
    ]
    assert json.loads(turns[1].attributes["input.value"]) == [
        {"role": "user", "content": "second question"}
    ]
    assert json.loads(turns[0].attributes["output.value"])["content"] == "first [redacted] answer"
    assert json.loads(turns[1].attributes["output.value"])["content"] == "second answer"
    assert turns[0].attributes["harnessed.output.partial"] is False
    assert "system instructions" not in turns[0].attributes["input.value"]


async def test_turn_summaries_include_steering_and_exclude_queued_followups(telemetry):
    provider, exporter = telemetry
    started = asyncio.Event()
    release = asyncio.Event()

    class Work(Plugin):
        @tool(readonly=True)
        async def wait(self) -> str:
            started.set()
            await release.wait()
            return "ready"

    async with asyncio.timeout(5):
        async with Agent(
            model=ScriptedModel(
                [[ModelChunk(tool_call=ToolCall("wait"))], "first answer", "followup answer"]
            ),
            plugins=[Work(), OpenTelemetryTracer(tracer_provider=provider, capture_content=True)],
        ).session() as session:
            first = await session.submit("initial question")
            await started.wait()
            await session.steer("steering question")
            followup = await session.submit("queued question")
            release.set()
            assert (await first.result()).text == "first answer"
            assert (await followup.result()).text == "followup answer"
    turns = [item for item in exporter.get_finished_spans() if item.name == "harnessed.turn"]
    assert len(turns) == 2
    assert [item["content"] for item in json.loads(turns[0].attributes["input.value"])] == [
        "initial question",
        "steering question",
    ]
    assert json.loads(turns[1].attributes["input.value"]) == [
        {"role": "user", "content": "queued question"}
    ]


async def test_failed_turn_retains_a_redacted_partial_answer(telemetry):
    provider, exporter = telemetry

    async def fail(request):
        yield ModelChunk(text="partial credential answer")
        raise RuntimeError("model failed")

    with pytest.raises(RuntimeError, match="model failed"):
        await Agent(
            model=CallbackModel(fail),
            plugins=[
                OpenTelemetryTracer(
                    tracer_provider=provider, capture_content=ContentCapture.redacted("credential")
                )
            ],
        ).run("credential question")
    turn = next(item for item in exporter.get_finished_spans() if item.name == "harnessed.turn")
    assert turn.attributes["harnessed.outcome"] == "failed"
    assert turn.attributes["harnessed.output.partial"] is True
    assert json.loads(turn.attributes["output.value"])["content"] == "partial [redacted] answer"
    assert json.loads(turn.attributes["input.value"])[0]["content"] == "[redacted] question"


async def test_accepted_token_limit_still_marks_the_turn_answer_partial(telemetry):
    provider, exporter = telemetry
    result = await Agent(
        model=ScriptedModel(
            [[ModelChunk(text="partial answer", stop_reason=StopReason.MAX_TOKENS)]]
        ),
        plugins=[OpenTelemetryTracer(tracer_provider=provider, capture_content=True)],
    ).run("question")
    turn = next(item for item in exporter.get_finished_spans() if item.name == "harnessed.turn")
    assert result.stop_reason is StopReason.MAX_TOKENS
    assert turn.attributes["harnessed.outcome"] == "completed"
    assert turn.attributes["harnessed.output.stop_reason"] == "max_tokens"
    assert turn.attributes["harnessed.output.partial"] is True
    assert json.loads(turn.attributes["output.value"])["content"] == "partial answer"


async def test_model_request_details_and_validated_turn_output_are_filterable(telemetry):
    provider, exporter = telemetry
    seen: list[TraceContentKind] = []

    class Answer(BaseModel):
        value: str

    class Work(Plugin):
        @tool(readonly=True)
        def lookup(self, key: str) -> str:
            """Look up an application record."""
            return key

    def filter_settings(content: TraceContent) -> TraceContent | None:
        seen.append(content.kind)
        return None if content.kind is TraceContentKind.MODEL_SETTINGS else content

    model = ScriptedModel(
        [[ModelChunk(tool_call=ToolCall("final_answer", {"value": "credential"}))]],
        settings=GenerationSettings(
            max_tokens=150, temperature=0.2, provider_options={"region": "credential"}
        ),
    )
    result = await Agent(
        model=model,
        plugins=[
            Work(),
            OpenTelemetryTracer(
                tracer_provider=provider,
                capture_content=ContentCapture.redacted("credential"),
                content_filter=filter_settings,
            ),
        ],
    ).run("answer", output=Answer, settings=GenerationSettings(max_tokens=50))
    spans = exporter.get_finished_spans()
    model_span = next(item for item in spans if item.name == "harnessed.model")
    turn = next(item for item in spans if item.name == "harnessed.turn")
    assert result.output.value == "credential"
    assert json.loads(turn.attributes["output.value"])["validated_output"] == {
        "value": "[redacted]"
    }
    assert model_span.attributes["gen_ai.request.max_tokens"] == 50
    assert model_span.attributes["gen_ai.request.temperature"] == 0.2
    tools = json.loads(model_span.attributes["request.tools.value"])
    assert any(
        item["name"] == "lookup" and "key" in item["parameters"]["properties"] for item in tools
    )
    schema = json.loads(model_span.attributes["request.output_schema.value"])
    assert "value" in schema["json_schema"]["properties"]
    assert "request.settings.value" not in model_span.attributes
    assert TraceContentKind.MODEL_SETTINGS in seen
    assert TraceContentKind.MODEL_TOOLS in seen
    assert TraceContentKind.MODEL_OUTPUT_SCHEMA in seen
    assert TraceContentKind.TURN_INPUT in seen and TraceContentKind.TURN_OUTPUT in seen
    assert "credential" not in str([item.attributes for item in spans])


@pytest.mark.parametrize("limit", [1, 2, 8, 24, 80, 400])
async def test_truncated_content_remains_bounded_valid_json(telemetry, limit):
    provider, exporter = telemetry
    value = {"content": '\\"\n密钥🔑' * 200, "items": list(range(30))}
    async with Agent(
        model=ScriptedModel([]),
        plugins=[
            OpenTelemetryTracer(
                tracer_provider=provider, capture_content=True, max_content_length=limit
            )
        ],
    ).session() as session:
        with library_span("harnessed.tool", scope=session.scope, tool="read") as active:
            active.set_attribute("input", ToolCall("read", value))
    captured = exporter.get_finished_spans()[0]
    encoded = captured.attributes["input.value"]
    assert len(encoded) <= limit
    assert json.loads(encoded) is not None
    assert captured.attributes["input.mime_type"] == "application/json"
    assert captured.attributes["harnessed.input.truncated"] is True
    assert captured.attributes["harnessed.input.original_length"] == len(
        json.dumps(value, ensure_ascii=False)
    )
    assert captured.attributes["harnessed.input.omitted_items"] >= 0


async def test_large_observations_do_not_hide_later_messages(telemetry):
    provider, exporter = telemetry
    async with Agent(
        model=ScriptedModel([]),
        plugins=[
            OpenTelemetryTracer(
                tracer_provider=provider, capture_content=True, max_content_length=500
            )
        ],
    ).session() as session:
        with library_span("harnessed.model", scope=session.scope) as active:
            active.set_attribute(
                "request",
                ModelRequest(
                    (
                        UserMessage("large observation " * 1000),
                        AssistantMessage("previous answer"),
                        UserMessage("latest question"),
                    )
                ),
            )
    captured = exporter.get_finished_spans()[0]
    messages = json.loads(captured.attributes["input.value"])
    assert len(messages) == 3
    assert messages[-1] == {"role": "user", "content": "latest question"}
    assert messages[1] == {"role": "assistant", "content": "previous answer"}
    assert len(captured.attributes["input.value"]) <= 500


@pytest.mark.parametrize("trace_hooks", [False, True])
async def test_library_spans_correlate_with_waterfall_hooks_and_session_cleanup(
    telemetry, trace_hooks
):
    provider, exporter = telemetry

    class Wrap(Waterfall[int]):
        pass

    class Cleanup(Plugin):
        @hook(SessionClosing)
        def closing(self, event: SessionClosing) -> None:
            with library_span("browserrig.cleanup"):
                pass

    async with Agent(
        model=ScriptedModel([]),
        plugins=[Cleanup(), OpenTelemetryTracer(tracer_provider=provider, trace_hooks=trace_hooks)],
    ).session() as session:

        @session.on(Wrap)
        async def wrap(event: Wrap, next_: Next[int]) -> int:
            with library_span("browserrig.readiness") as measured:
                measured.set_attribute("browserrig.ready", True)
                return await next_() + 1

        @session.on(Wrap)
        async def inner(event: Wrap, next_: Next[int]) -> int:
            with library_span("browserrig.extract"):
                return await next_() + 1

        assert await session.scope.dispatch(Wrap(), default=lambda: 40) == 42
        session_id = session.id
    spans = exporter.get_finished_spans()
    readiness = next(item for item in spans if item.name == "browserrig.readiness")
    extraction = next(item for item in spans if item.name == "browserrig.extract")
    cleanup = next(item for item in spans if item.name == "browserrig.cleanup")
    hooks = [
        item
        for item in spans
        if item.name == "harnessed.hook"
        and item.attributes["harnessed.hook.event"].endswith(".Wrap")
    ]
    assert readiness.attributes["session.id"] == session_id
    assert readiness.attributes["browserrig.ready"] is True
    assert extraction.attributes["session.id"] == session_id
    assert cleanup.attributes["session.id"] == session_id
    assert "harnessed.turn_id" not in cleanup.attributes
    if trace_hooks:
        assert len(hooks) == 2
        assert extraction.parent.span_id == hooks[0].context.span_id
        assert hooks[0].parent.span_id == readiness.context.span_id
        assert readiness.parent.span_id == hooks[1].context.span_id
        assert hooks[1].attributes["harnessed.hook.priority"] == 0
    else:
        assert hooks == []
        assert extraction.parent.span_id == readiness.context.span_id
    with library_span("outside.session"):
        pass
    assert all(item.name != "outside.session" for item in exporter.get_finished_spans())
    assert trace.get_current_span().get_span_context().is_valid is False


async def test_concurrent_sessions_keep_library_tracers_and_parent_spans_isolated(telemetry):
    provider, exporter = telemetry
    barrier = asyncio.Barrier(2)

    async def respond(request):
        with library_span("library.model_work"):
            await barrier.wait()
            yield ModelChunk(text="done")

    first = Agent(
        model=CallbackModel(respond), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
    )
    second = Agent(
        model=CallbackModel(respond), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
    )
    async with asyncio.timeout(5):
        results = await asyncio.gather(first.run("first"), second.run("second"))
    assert [result.text for result in results] == ["done", "done"]
    spans = exporter.get_finished_spans()
    work = [item for item in spans if item.name == "library.model_work"]
    models = {item.context.span_id: item for item in spans if item.name == "harnessed.model"}
    assert len(work) == 2
    assert len({item.attributes["session.id"] for item in work}) == 2
    assert all(
        item.attributes["session.id"] == models[item.parent.span_id].attributes["session.id"]
        for item in work
    )
    with library_span("after.sessions"):
        pass
    assert all(item.name != "after.sessions" for item in exporter.get_finished_spans())


async def test_nested_session_cleanup_does_not_inherit_the_parent_turn_identity(telemetry):
    provider, exporter = telemetry

    class Cleanup(Plugin):
        @hook(SessionClosing)
        def closing(self, event: SessionClosing) -> None:
            with library_span("child.cleanup"):
                pass

    child = Agent(
        model=ScriptedModel(["child answer"]),
        plugins=[Cleanup(), OpenTelemetryTracer(tracer_provider=provider)],
    )

    class Work(Plugin):
        @tool(readonly=True)
        async def delegate(self) -> str:
            return (await child.run("child question")).text

    result = await Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("delegate"))], "parent answer"]),
        plugins=[Work(), OpenTelemetryTracer(tracer_provider=provider)],
    ).run("parent question")
    spans = exporter.get_finished_spans()
    cleanup = next(item for item in spans if item.name == "child.cleanup")
    parent = next(item for item in spans if item.name == "harnessed.tool")
    assert result.text == "parent answer"
    assert cleanup.parent.span_id == parent.context.span_id
    assert cleanup.attributes["session.id"] != parent.attributes["session.id"]
    assert "harnessed.turn_id" not in cleanup.attributes
    assert "harnessed.step" not in cleanup.attributes


async def test_traced_tools_record_nested_attributes_and_cached_source_metadata(telemetry):
    provider, exporter = telemetry

    @traced
    def extract(value: str, *, offset: int = 0) -> int:
        """Count an observation."""
        record(elements=len(value))
        return len(value) + offset

    class Actions(Plugin):
        @tool(readonly=True)
        @traced
        async def capture(self, value: str) -> int:
            record(operation="capture")
            await asyncio.sleep(0)
            with library_span("library.capture.parse"):
                record(phase="parse")
            result = extract(value, offset=1)
            record(finished=True)
            return result

    result = await Agent(
        model=ScriptedModel(
            [[ModelChunk(tool_call=ToolCall("capture", {"value": "private"}))], "done"]
        ),
        plugins=[Actions(), OpenTelemetryTracer(tracer_provider=provider)],
    ).run("go")
    spans = exporter.get_finished_spans()
    capture = next(item for item in spans if item.name.endswith(".Actions.capture"))
    extraction = next(item for item in spans if item.name.endswith(".extract"))
    block = next(item for item in spans if item.name == "library.capture.parse")
    tool_span = next(item for item in spans if item.name == "harnessed.tool")
    assert result.text == "done" and result.tool_calls[0][1].text == "8"
    assert capture.parent.span_id == tool_span.context.span_id
    assert extraction.parent.span_id == capture.context.span_id
    assert block.parent.span_id == capture.context.span_id
    assert extraction.name == f"{extract.__module__}.{extract.__qualname__}"
    assert extraction.attributes["code.function.name"] == extraction.name
    source = inspect.unwrap(extract)
    assert extraction.attributes["code.file.path"] == inspect.getsourcefile(source)
    assert extraction.attributes["code.line.number"] == source.__code__.co_firstlineno
    assert extraction.attributes["harnessed.elements"] == 7
    assert capture.attributes["harnessed.operation"] == "capture"
    assert capture.attributes["harnessed.finished"] is True
    assert block.attributes["harnessed.phase"] == "parse"
    assert "harnessed.elements" not in capture.attributes
    assert "harnessed.phase" not in capture.attributes
    assert "private" not in str([item.attributes for item in spans])


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_traced_preserves_source_exceptions_and_restores_parent_recording(
    telemetry, asynchronous
):
    provider, exporter = telemetry
    failure = LookupError("original failure")

    @traced
    def fail_sync() -> None:
        record(failing=True)
        raise failure

    @traced
    async def fail_async() -> None:
        await asyncio.sleep(0)
        record(failing=True)
        raise failure

    async with Agent(
        model=ScriptedModel([]), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
    ).session() as session:
        with library_span("library.parent", scope=session.scope):
            with pytest.raises(LookupError) as raised:
                if asynchronous:
                    await fail_async()
                else:
                    fail_sync()
            record(recovered=True)
    spans = exporter.get_finished_spans()
    failed = next(item for item in spans if item.name.endswith((".fail_sync", ".fail_async")))
    parent = next(item for item in spans if item.name == "library.parent")
    assert raised.value is failure
    assert failed.status.status_code is trace.StatusCode.ERROR
    assert failed.attributes["harnessed.failing"] is True
    assert "harnessed.recovered" not in failed.attributes
    assert parent.attributes["harnessed.recovered"] is True
    assert parent.status.status_code is not trace.StatusCode.ERROR
    record(outside=True)
    assert "harnessed.outside" not in parent.attributes


async def test_traced_cancellation_closes_the_span_after_async_cleanup(telemetry):
    provider, exporter = telemetry
    entered = asyncio.Event()

    @traced
    async def wait() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            record(cleaned_up=True)

    async with asyncio.timeout(5):
        async with Agent(
            model=ScriptedModel([]), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
        ).session() as session:
            with library_span("library.parent", scope=session.scope):
                task = asyncio.create_task(wait())
                await entered.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                record(parent_resumed=True)
    spans = exporter.get_finished_spans()
    cancelled = next(item for item in spans if item.name.endswith(".wait"))
    parent = next(item for item in spans if item.name == "library.parent")
    assert cancelled.parent.span_id == parent.context.span_id
    assert cancelled.attributes["harnessed.cancelled"] is True
    assert cancelled.attributes["harnessed.cleaned_up"] is True
    assert "harnessed.parent_resumed" not in cancelled.attributes
    assert parent.attributes["harnessed.parent_resumed"] is True


async def test_traced_concurrent_calls_keep_recorded_attributes_separate(telemetry):
    provider, exporter = telemetry
    barrier = asyncio.Barrier(2)

    @traced
    async def work(identity: str) -> str:
        await barrier.wait()
        record(identity=identity)
        return identity

    async with asyncio.timeout(5):
        async with Agent(
            model=ScriptedModel([]), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
        ).session() as session:
            with library_span("library.parent", scope=session.scope):
                assert await asyncio.gather(work("first"), work("second")) == ["first", "second"]
                record(parent=True)
    spans = exporter.get_finished_spans()
    workers = [item for item in spans if item.name.endswith(".work")]
    parent = next(item for item in spans if item.name == "library.parent")
    assert len(workers) == 2
    assert {item.attributes["harnessed.identity"] for item in workers} == {"first", "second"}
    assert all(item.parent.span_id == parent.context.span_id for item in workers)
    assert "harnessed.identity" not in parent.attributes
    assert parent.attributes["harnessed.parent"] is True


async def test_untraced_session_cleanup_cannot_record_on_the_parent_span(telemetry):
    provider, exporter = telemetry

    class Cleanup(Plugin):
        @hook(SessionClosing)
        @traced
        def closing(self, event: SessionClosing) -> None:
            record(child_cleanup=True)

    async with Agent(
        model=ScriptedModel([]), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
    ).session() as session:
        with library_span("library.parent", scope=session.scope):
            result = await Agent(model=ScriptedModel(["done"]), plugins=[Cleanup()]).run("child")
            record(parent_after=True)
    spans = exporter.get_finished_spans()
    assert result.text == "done"
    assert len(spans) == 1
    assert spans[0].name == "library.parent"
    assert spans[0].attributes["harnessed.parent_after"] is True
    assert "harnessed.child_cleanup" not in spans[0].attributes


async def test_content_is_explicit_bounded_and_excludes_opaque_state(telemetry):
    provider, exporter = telemetry
    native = AssistantMessage(
        "answer",
        reasoning="private reasoning",
        provider_state=ProviderState(
            format="test.response", version=1, provider="test", model="test", data={"key": "opaque"}
        ),
    )
    # Scripted streams retain response text; exercise the portable serializer
    # directly as well to check exclusion of opaque provider state.
    plugin = OpenTelemetryTracer(tracer_provider=provider, capture_content=True)
    async with Agent(model=ScriptedModel(["answer"]), plugins=[plugin]).session() as session:
        await session.run("secret prompt")
        tracer = session.scope[Tracer]
        with tracer.span("harnessed.model", session_id=session.id) as span:
            span.set_attribute("output", native)
    model_spans = [s for s in exporter.get_finished_spans() if s.name == "harnessed.model"]
    assert json.loads(model_spans[0].attributes["input.value"])[0]["content"] == "secret prompt"
    assert json.loads(model_spans[1].attributes["output.value"]) == {
        "role": "assistant",
        "content": "answer",
    }
    assert "opaque" not in str(model_spans[1].attributes)
    assert "private reasoning" not in str(model_spans[1].attributes)
    await Agent(
        model=ScriptedModel(["answer"]),
        plugins=[
            OpenTelemetryTracer(
                tracer_provider=provider, capture_content=True, max_content_length=8
            )
        ],
    ).run("long prompt")
    model = [s for s in exporter.get_finished_spans() if s.name == "harnessed.model"][-1]
    assert len(model.attributes["input.value"]) <= 8
    json.loads(model.attributes["input.value"])
    assert model.attributes["harnessed.input.truncated"] is True
    assert model.attributes["input.mime_type"] == "application/json"


async def test_content_filter_redacts_model_history_and_tool_fields(telemetry):
    provider, exporter = telemetry
    seen = []

    def redact_value(value: JsonValue) -> JsonValue:
        if isinstance(value, dict):
            return {key: redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact_value(item) for item in value]
        return value.replace("secret", "[redacted]") if isinstance(value, str) else value

    def redact(content: TraceContent) -> TraceContent:
        seen.append((content.kind, content.tool_name))
        return replace(content, value=redact_value(content.value))

    class Work(Plugin):
        @tool(readonly=True)
        def work(self, api_key: str) -> str:
            return f"tool result {api_key}"

    call = ToolCall("work", {"api_key": "secret"})
    model = ScriptedModel([[ModelChunk(tool_call=call)], "secret answer"])
    result = await Agent(
        model=model,
        plugins=[
            Work(),
            OpenTelemetryTracer(
                tracer_provider=provider, capture_content=True, content_filter=redact
            ),
        ],
    ).run("secret prompt")
    spans = exporter.get_finished_spans()
    assert result.text == "secret answer" and result.tool_calls[0][1].text == "tool result secret"
    assert (TraceContentKind.TOOL_ARGUMENTS, "work") in seen
    assert (TraceContentKind.TOOL_RESULT, "work") in seen
    assert (TraceContentKind.MODEL_INPUT, None) in seen
    assert (TraceContentKind.MODEL_OUTPUT, None) in seen
    assert "secret" not in str([span.attributes for span in spans])
    tool_span = next(span for span in spans if span.name == "harnessed.tool")
    assert json.loads(tool_span.attributes["input.value"]) == {"api_key": "[redacted]"}
    models = [span for span in spans if span.name == "harnessed.model"]
    history = json.loads(models[-1].attributes["input.value"])
    assistant = next(message for message in history if message["role"] == "assistant")
    assert assistant["tool_calls"][0]["arguments"] == {"api_key": "[redacted]"}
    assert json.loads(models[-1].attributes["output.value"])["content"] == "[redacted] answer"


async def test_content_filter_can_mutate_a_detached_copy(telemetry):
    provider, exporter = telemetry

    def redact(content: TraceContent) -> TraceContent:
        if content.kind is TraceContentKind.TOOL_ARGUMENTS:
            assert isinstance(content.value, dict)
            content.value["api_key"] = "[redacted]"
        return content

    tracer = OpenTelemetryTracer(
        tracer_provider=provider, capture_content=True, content_filter=redact
    )
    call = ToolCall("work", {"api_key": "secret"})
    async with Agent(model=ScriptedModel([]), plugins=[tracer]).session() as session:
        with session.scope[Tracer].span("harnessed.tool", tool="work") as span:
            span.set_attribute("input", call)
    assert dict(call.arguments) == {"api_key": "secret"}
    captured = exporter.get_finished_spans()[0]
    assert json.loads(captured.attributes["input.value"]) == {"api_key": "[redacted]"}


@pytest.mark.parametrize("fail_filter", [False, True])
async def test_dropped_or_failed_filters_omit_content_and_preserve_original_failures(
    telemetry, fail_filter
):
    provider, exporter = telemetry
    seen = []

    def redact(content: TraceContent) -> None:
        seen.append(content.kind)
        if fail_filter:
            raise ValueError("filter secret")
        return None

    async def fail(request):
        yield ModelChunk(text="partial secret")
        raise RuntimeError("source secret")

    with pytest.raises(RuntimeError, match="source secret"):
        await Agent(
            model=CallbackModel(fail),
            plugins=[
                OpenTelemetryTracer(
                    tracer_provider=provider, capture_content=True, content_filter=redact
                )
            ],
        ).run("secret prompt")
    spans = exporter.get_finished_spans()
    assert len(spans) == 3
    assert TraceContentKind.MODEL_INPUT in seen and TraceContentKind.EXCEPTION_MESSAGE in seen
    assert all("input.value" not in span.attributes for span in spans)
    assert all("output.value" not in span.attributes for span in spans)
    assert all("exception.message" not in span.events[0].attributes for span in spans)
    assert all(span.status.status_code == trace.StatusCode.ERROR for span in spans)
    assert "secret" not in str([(span.attributes, span.events) for span in spans])
    if fail_filter:
        assert spans[0].attributes["harnessed.input.filter_error"] is True
        assert all(span.attributes["harnessed.exception.filter_error"] for span in spans)


async def test_filter_runs_before_serialization_and_truncation(telemetry):
    provider, exporter = telemetry
    seen = []

    def redact(content: TraceContent) -> TraceContent:
        seen.append(content)
        return replace(
            content, value="safe" if content.kind is TraceContentKind.EXCEPTION_MESSAGE else {}
        )

    tracer = OpenTelemetryTracer(
        tracer_provider=provider, capture_content=True, content_filter=redact, max_content_length=4
    )
    async with Agent(model=ScriptedModel([]), plugins=[tracer]).session() as session:
        with pytest.raises(RuntimeError, match="secret error"):
            with session.scope[Tracer].span("harnessed.tool", tool="work") as span:
                span.set_attribute("input", ToolCall("work", {"api_key": "long secret value"}))
                raise RuntimeError("secret error beyond the limit")
    assert [item.value for item in seen] == [
        {"api_key": "long secret value"},
        "secret error beyond the limit",
    ]
    captured = exporter.get_finished_spans()[0]
    assert captured.attributes["input.value"] == "{}"
    assert captured.attributes["input.mime_type"] == "application/json"
    assert "harnessed.input.truncated" not in captured.attributes
    assert captured.events[0].attributes["exception.message"] == "safe"


async def test_content_filter_is_not_called_when_capture_is_disabled(telemetry):
    provider, exporter = telemetry

    def redact(content: TraceContent) -> TraceContent:
        pytest.fail("Content capture is disabled")

    await Agent(
        model=ScriptedModel(["answer"]),
        plugins=[OpenTelemetryTracer(tracer_provider=provider, content_filter=redact)],
    ).run("secret prompt")
    spans = exporter.get_finished_spans()
    assert len(spans) == 3
    assert all("input.value" not in span.attributes for span in spans)


@pytest.mark.parametrize("wrapped", [False, True])
async def test_registered_secrets_are_redacted_across_runtime_and_history(telemetry, wrapped):
    provider, exporter = telemetry
    token = "billing-token-xyz"
    capture = ContentCapture.redacted(SecretStr(token) if wrapped else token)

    class Billing(Plugin):
        @tool(readonly=True)
        def lookup(self, invoice_id: str, authorization: str) -> str:
            assert authorization == f"Bearer {token}"
            return f"{invoice_id}: paid; Authorization: {authorization}"

    call = ToolCall("lookup", {"invoice_id": "INV-42", "authorization": f"Bearer {token}"})
    model = ScriptedModel([[ModelChunk(tool_call=call)], f"Paid using {token}"])
    async with Agent(
        model=model,
        plugins=[Billing(), OpenTelemetryTracer(tracer_provider=provider, capture_content=capture)],
    ).session() as session:
        result = await session.run(f"Check INV-42 with {token}")
        assert result.text == f"Paid using {token}"
        assert result.tool_calls[0][1].text == f"INV-42: paid; Authorization: Bearer {token}"
        assert token in str(session.messages)
    spans = exporter.get_finished_spans()
    assert len(spans) == 6
    assert token not in str([span.attributes for span in spans])
    tool_span = next(span for span in spans if span.name == "harnessed.tool")
    assert json.loads(tool_span.attributes["input.value"]) == {
        "invoice_id": "INV-42",
        "authorization": "Bearer [redacted]",
    }
    assert json.loads(tool_span.attributes["output.value"]) == (
        "INV-42: paid; Authorization: Bearer [redacted]"
    )
    models = [span for span in spans if span.name == "harnessed.model"]
    assert json.loads(models[0].attributes["input.value"])[0]["content"] == (
        "Check INV-42 with [redacted]"
    )
    history = json.loads(models[-1].attributes["input.value"])
    assert history[1]["tool_calls"][0]["arguments"]["authorization"] == "Bearer [redacted]"
    assert history[2]["content"] == "INV-42: paid; Authorization: Bearer [redacted]"
    assert json.loads(models[-1].attributes["output.value"])["content"] == ("Paid using [redacted]")


async def test_custom_secret_wrapper_is_snapshotted_and_hidden(telemetry):
    provider, exporter = telemetry

    @dataclass
    class Credential:
        value: str

        def get_secret_value(self) -> str:
            return self.value

    credential = Credential("original-token")
    capture = ContentCapture.redacted(credential)
    credential.value = "rotated-token"
    tracer = OpenTelemetryTracer(tracer_provider=provider, capture_content=capture)
    assert "original-token" not in repr(capture)
    assert "original-token" not in repr(tracer)
    assert "original-token" not in repr(tracer.config.model_dump())
    await Agent(model=ScriptedModel(["answer"]), plugins=[tracer]).run("original-token")
    model = next(span for span in exporter.get_finished_spans() if span.name == "harnessed.model")
    assert json.loads(model.attributes["input.value"])[0]["content"] == "[redacted]"


@pytest.mark.parametrize(
    ("secrets", "text", "expected"),
    [
        (("abc", "abcdef"), "abcdef", "[redacted]"),
        (("abc", "bcdef"), "abcdef", "[redacted]"),
        (("abc", "abc"), "abcabc", "[redacted][redacted]"),
        (("a.*[x]",), "a.*[x] aZZx", "[redacted] aZZx"),
        (("密钥🔑",), "value=密钥🔑", "value=[redacted]"),
        ((), "ordinary text", "ordinary text"),
    ],
)
async def test_redaction_is_literal_and_covers_overlapping_values(
    telemetry, secrets, text, expected
):
    provider, exporter = telemetry
    tracer = OpenTelemetryTracer(
        tracer_provider=provider, capture_content=ContentCapture.redacted(*secrets)
    )
    await Agent(model=ScriptedModel([text]), plugins=[tracer]).run("question")
    model = next(span for span in exporter.get_finished_spans() if span.name == "harnessed.model")
    assert json.loads(model.attributes["output.value"])["content"] == expected


@pytest.mark.parametrize("include_exception_messages", [False, True])
async def test_redacted_exception_text_is_separately_enabled(telemetry, include_exception_messages):
    provider, exporter = telemetry
    capture = ContentCapture.redacted(
        "token-xyz", include_exception_messages=include_exception_messages
    )
    tracer = OpenTelemetryTracer(tracer_provider=provider, capture_content=capture)
    async with Agent(model=ScriptedModel([]), plugins=[tracer]).session() as session:
        with pytest.raises(RuntimeError, match="Bearer token-xyz"):
            with session.scope[Tracer].span("harnessed.tool", tool="billing"):
                raise RuntimeError("Authorization: Bearer token-xyz")
    span = exporter.get_finished_spans()[0]
    attributes = span.events[0].attributes
    assert attributes["exception.type"] == "builtins.RuntimeError"
    assert span.status.status_code == trace.StatusCode.ERROR
    if include_exception_messages:
        assert attributes["exception.message"] == "Authorization: Bearer [redacted]"
    else:
        assert "exception.message" not in attributes


async def test_redaction_covers_filter_output_and_runs_before_limits(telemetry):
    provider, exporter = telemetry

    def transform(content: TraceContent) -> TraceContent:
        return replace(content, value={"secret-token": ["prefix secret-token suffix"]})

    tracer = OpenTelemetryTracer(
        tracer_provider=provider,
        capture_content=ContentCapture.redacted("secret-token"),
        content_filter=transform,
        max_content_length=28,
    )
    await Agent(model=ScriptedModel(["answer"]), plugins=[tracer]).run("question")
    model = next(span for span in exporter.get_finished_spans() if span.name == "harnessed.model")
    assert "secret-token" not in model.attributes["output.value"]
    assert len(model.attributes["output.value"]) <= 28
    json.loads(model.attributes["output.value"])
    assert model.attributes["output.mime_type"] == "application/json"
    assert model.attributes["harnessed.output.truncated"] is True


async def test_redaction_failure_omits_content_without_changing_agent_result(telemetry):
    provider, exporter = telemetry

    def invalid_content(content: TraceContent) -> TraceContent:
        value = {}
        value["recursive"] = value
        return replace(content, value=value)

    tracer = OpenTelemetryTracer(
        tracer_provider=provider,
        capture_content=ContentCapture.redacted("secret-token"),
        content_filter=invalid_content,
    )
    result = await Agent(model=ScriptedModel(["answer"]), plugins=[tracer]).run("secret-token")
    assert result.text == "answer"
    model = next(span for span in exporter.get_finished_spans() if span.name == "harnessed.model")
    assert "input.value" not in model.attributes
    assert "output.value" not in model.attributes
    assert model.attributes["harnessed.input.filter_error"] is True
    assert model.attributes["harnessed.output.filter_error"] is True
    assert "secret-token" not in str(model.attributes)


@pytest.mark.parametrize(
    "replacement",
    [("secret-token",), {"secret-token": 1, "other-token": 2}],
)
async def test_redaction_rejects_invalid_values_and_key_collisions(telemetry, replacement):
    provider, exporter = telemetry
    tracer = OpenTelemetryTracer(
        tracer_provider=provider,
        capture_content=ContentCapture.redacted("secret-token", "other-token"),
        content_filter=lambda content: replace(content, value=replacement),
    )
    result = await Agent(model=ScriptedModel(["answer"]), plugins=[tracer]).run("question")
    assert result.text == "answer"
    model = next(span for span in exporter.get_finished_spans() if span.name == "harnessed.model")
    assert "output.value" not in model.attributes
    assert model.attributes["harnessed.output.filter_error"] is True


async def test_redacted_content_is_exported_without_registered_secret(otlp_collector: Collector):
    base_url, requests = otlp_collector
    async with otlp_provider(service_name="billing", endpoint=f"{base_url}/v1/traces") as provider:
        result = await Agent(
            model=ScriptedModel(["Paid using wire-secret-token"]),
            plugins=[
                OpenTelemetryTracer(
                    tracer_provider=provider,
                    capture_content=ContentCapture.redacted("wire-secret-token"),
                )
            ],
        ).run("INV-42; Authorization: Bearer wire-secret-token")
    assert result.text == "Paid using wire-secret-token"
    assert requests
    spans = [
        span
        for _, _, body in requests
        for resource in ExportTraceServiceRequest.FromString(body).resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    assert len(spans) == 3
    model = next(span for span in spans if span.name == "harnessed.model")
    attributes = {attribute.key: attribute.value.string_value for attribute in model.attributes}
    assert json.loads(attributes["input.value"])[0]["content"] == (
        "INV-42; Authorization: Bearer [redacted]"
    )
    assert json.loads(attributes["output.value"])["content"] == "Paid using [redacted]"
    assert "wire-secret-token" not in str(spans)


@pytest.mark.parametrize("secret", ["", SecretStr(""), b"bytes", None, 42])
def test_invalid_secrets_are_rejected_without_echoing_values(secret):
    with pytest.raises((TypeError, ValueError)) as error:
        ContentCapture.redacted(secret)
    assert str(error.value) in {
        "Secret values must not be empty",
        "Secrets must be strings or SecretText wrappers",
    }


@pytest.mark.parametrize("failure", [False, True])
def test_invalid_secret_wrappers_raise_without_exposing_their_contents(failure):
    class InvalidSecret:
        def get_secret_value(self):
            if failure:
                raise RuntimeError("private-token")
            return b"private-token"

    with pytest.raises((TypeError, ValueError)) as error:
        ContentCapture.redacted(InvalidSecret())
    assert "private-token" not in str(error.value)
    assert str(error.value) in {
        "Could not read a text secret",
        "SecretText wrappers must return strings",
    }


async def test_parallel_tools_are_siblings_and_inherit_context(telemetry):
    provider, exporter = telemetry
    barrier = asyncio.Barrier(2)

    class Work(Plugin):
        @tool(parallel=True)
        async def work(self, index: int) -> str:
            await barrier.wait()
            with provider.get_tracer("application").start_as_current_span(f"inner.{index}"):
                return "done"

    calls = [ToolCall("work", {"index": i}) for i in range(2)]
    await Agent(
        model=ScriptedModel([[ModelChunk(tool_call=call) for call in calls], "done"]),
        plugins=[
            Work(),
            Approvals(default=PolicyDecision.ALLOW),
            OpenTelemetryTracer(tracer_provider=provider),
        ],
    ).run("go")
    spans = exporter.get_finished_spans()
    tools = [s for s in spans if s.name == "harnessed.tool"]
    assert len(tools) == 2
    assert tools[0].parent.span_id == tools[1].parent.span_id
    assert {s.attributes["harnessed.call_id"] for s in tools} == {c.id for c in calls}
    assert all(s.attributes["harnessed.step"] == 1 for s in tools)
    assert all(s.attributes["harnessed.tool.status"] == "ok" for s in tools)
    for index, call in enumerate(calls):
        inner = next(s for s in spans if s.name == f"inner.{index}")
        outer = next(s for s in tools if s.attributes["harnessed.call_id"] == call.id)
        assert inner.parent.span_id == outer.context.span_id


@pytest.mark.parametrize(
    ("status", "exit_code", "description"),
    [
        (ToolStatus.ERROR, None, "error"),
        (ToolStatus.TIMEOUT, None, "timeout"),
        (ToolStatus.UNAVAILABLE, None, "unavailable"),
        (ToolStatus.OK, 1, "nonzero_exit_code"),
    ],
)
async def test_tool_result_errors_mark_the_span_without_raising(
    telemetry, status, exit_code, description
):
    provider, exporter = telemetry

    class Work(Plugin):
        @tool
        def work(self) -> ToolResult:
            return ToolResult("private error details", status, exit_code=exit_code)

    await Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "recovered"]),
        plugins=[
            Work(),
            Approvals(default=PolicyDecision.ALLOW),
            OpenTelemetryTracer(tracer_provider=provider),
        ],
    ).run("go")
    tool_span = next(s for s in exporter.get_finished_spans() if s.name == "harnessed.tool")
    assert tool_span.status.status_code == trace.StatusCode.ERROR
    assert tool_span.attributes["harnessed.tool.status"] == status.value
    assert "private error details" not in str(tool_span.attributes)
    assert tool_span.status.description == description


async def test_approval_wait_is_separate_and_records_decision(telemetry):
    provider, exporter = telemetry

    class Work(Plugin):
        @tool
        def work(self) -> str:
            return "done"

    await Agent(
        model=ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "done"]),
        plugins=[
            Work(),
            Approvals(approver=lambda _: ApprovalAnswer.ALLOW),
            OpenTelemetryTracer(tracer_provider=provider),
        ],
    ).run("go")
    spans = exporter.get_finished_spans()
    approval = next(s for s in spans if s.name == "harnessed.approval_wait")
    tool_span = next(s for s in spans if s.name == "harnessed.tool")
    assert approval.end_time <= tool_span.start_time
    assert approval.attributes["harnessed.decision"] == "allow"
    assert approval.parent.span_id == tool_span.parent.span_id


async def test_failure_closes_spans_without_exporting_exception_text(telemetry):
    provider, exporter = telemetry

    async def fail(request):
        yield ModelChunk(text="partial secret", usage=ModelUsage(3, 1))
        raise RuntimeError("private exception")

    with pytest.raises(RuntimeError, match="private exception"):
        await Agent(
            model=CallbackModel(fail), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
        ).run("secret prompt")
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["harnessed.model", "harnessed.step", "harnessed.turn"]
    assert all(s.status.status_code == trace.StatusCode.ERROR for s in spans)
    assert all("exception.message" not in s.events[0].attributes for s in spans)
    assert spans[0].attributes["llm.token_count.total"] == 4
    assert "partial secret" not in str(spans[0].attributes)


async def test_abort_closes_spans_and_preserves_partial_usage(telemetry):
    provider, exporter = telemetry
    started = asyncio.Event()

    async def wait(request):
        yield ModelChunk(text="partial", usage=ModelUsage(4, 2))
        started.set()
        await asyncio.Event().wait()

    async with Agent(
        model=CallbackModel(wait), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
    ).session() as session:
        await session.submit("go")
        await asyncio.wait_for(started.wait(), timeout=5)
        await session.abort()
    spans = exporter.get_finished_spans()
    assert len(spans) == 3
    assert all(s.attributes["harnessed.cancelled"] for s in spans)
    assert spans[0].attributes["llm.token_count.total"] == 6


async def test_graceful_stop_is_not_recorded_as_an_exception(telemetry):
    provider, exporter = telemetry
    started = asyncio.Event()
    release = asyncio.Event()

    async def respond(request):
        started.set()
        await release.wait()
        yield ModelChunk(text="finished step")

    async with Agent(
        model=CallbackModel(respond), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
    ).session() as session:
        await session.submit("go")
        await asyncio.wait_for(started.wait(), timeout=5)

        def release_on_stop(event: SessionAppended) -> None:
            if (
                isinstance(event.event, SessionControlChangedEvent)
                and event.event.state == ControlState.STOPPING
            ):
                release.set()

        session.on(SessionAppended, release_on_stop)
        await session.stop()
    spans = exporter.get_finished_spans()
    assert len(spans) == 3
    turn = next(s for s in spans if s.name == "harnessed.turn")
    assert turn.attributes["harnessed.outcome"] == "stopped"
    assert all(s.status.status_code != trace.StatusCode.ERROR for s in spans)
    assert all(not s.events for s in spans)


async def test_sessions_are_isolated_and_plugin_does_not_shutdown_provider(telemetry):
    provider, exporter = telemetry
    barrier = asyncio.Barrier(2)

    async def respond(request):
        await barrier.wait()
        yield ModelChunk(text="done")

    agent = Agent(
        model=CallbackModel(respond), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
    )
    await asyncio.gather(agent.run("one"), agent.run("two"))
    turns = [s for s in exporter.get_finished_spans() if s.name == "harnessed.turn"]
    assert len(turns) == 2
    assert turns[0].context.trace_id != turns[1].context.trace_id
    assert turns[0].attributes["session.id"] != turns[1].attributes["session.id"]
    for span in exporter.get_finished_spans():
        turn = next(t for t in turns if t.context.trace_id == span.context.trace_id)
        assert span.attributes["session.id"] == turn.attributes["session.id"]
        assert span.attributes["harnessed.turn_id"] == turn.attributes["harnessed.turn_id"]
    with provider.get_tracer("backend").start_as_current_span("still.working"):
        pass
    assert exporter.get_finished_spans()[-1].name == "still.working"


async def test_compaction_span_records_its_usage(telemetry):
    provider, exporter = telemetry
    agent = Agent(
        model=ScriptedModel(["first", "kept", reply("summary", usage=ModelUsage(9, 2)), "second"]),
        plugins=[
            Compaction(trigger=every_turns(1), keep_last_steps=1),
            OpenTelemetryTracer(tracer_provider=provider),
        ],
    )
    async with agent.session() as session:
        await session.run("long prompt " * 100)
        await session.run("kept")
        await session.run("next")
    compact = next(s for s in exporter.get_finished_spans() if s.name == "harnessed.compaction")
    assert compact.attributes["llm.token_count.total"] == 11


def test_core_and_public_facades_import_without_otel():
    code = """
import importlib.abc
import sys
class NoOtel(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('opentelemetry'):
            raise ImportError('optional dependency not installed')
sys.meta_path.insert(0, NoOtel())
import harnessed
import harnessed.plugins
import harnessed.models
import harnessed.integrations
from harnessed import Agent
from harnessed.core.tracing import NoopTracer
from harnessed.tracing import current, record, span, traced
@traced
def work(value: int) -> int:
    record(value=value)
    return value + 1
assert work(1) == 2
with span('library.noop'), current().span('library.also_noop'):
    pass
assert not any(n.startswith('opentelemetry') for n in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", ["/v1/traces", "/api/public/otel/v1/traces"])
async def test_real_otlp_http_export_to_local_collector(
    path: str, otlp_collector: Collector
) -> None:
    base_url, requests = otlp_collector
    provider = TracerProvider()
    endpoint = f"{base_url}{path}"
    provider.add_span_processor(
        SimpleSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, headers={"x-test": "harnessed"}, timeout=3)
        )
    )
    try:
        await Agent(
            model=ScriptedModel(["done"]), plugins=[OpenTelemetryTracer(tracer_provider=provider)]
        ).run("go")
        provider.force_flush()
        assert len(requests) == 3
        names = []
        for request_path, headers, body in requests:
            assert request_path == path
            assert headers["x-test"] == "harnessed"
            assert headers["Content-Type"] == "application/x-protobuf"
            data = ExportTraceServiceRequest.FromString(body)
            names.extend(
                s.name for r in data.resource_spans for scope in r.scope_spans for s in scope.spans
            )
        assert names == ["harnessed.model", "harnessed.step", "harnessed.turn"]
    finally:
        provider.shutdown()


@pytest.mark.parametrize("endpoint_source", ["explicit", "traces_env", "base_env"])
@pytest.mark.parametrize("fail", [False, True])
async def test_otlp_provider_shared_sessions_and_batch_delivery_on_exit(
    otlp_collector: Collector,
    monkeypatch: pytest.MonkeyPatch,
    shutdown_threads: list[int],
    endpoint_source: str,
    fail: bool,
) -> None:
    base_url, requests = otlp_collector
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", base_url)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "x-test=harnessed")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "3")
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", "60000")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_on")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "environment-service")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment.name=test")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    endpoint = None
    expected_path = "/v1/traces"
    if endpoint_source == "traces_env":
        expected_path = "/configured-traces"
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", f"{base_url}{expected_path}")
    elif endpoint_source == "explicit":
        expected_path = "/api/public/otel/v1/traces"
        endpoint = f"{base_url}{expected_path}"
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", f"{base_url}/overridden")

    global_provider = trace.get_tracer_provider()
    main_thread = get_ident()
    attributes = {"service.name": "overridden", "openinference.project.name": "shared-agent"}
    expected_error = (
        pytest.raises(RuntimeError, match="application failed") if fail else nullcontext()
    )
    with expected_error:
        async with otlp_provider(
            service_name="my-service", endpoint=endpoint, resource_attributes=attributes
        ) as provider:
            assert trace.get_tracer_provider() is global_provider
            assert provider.resource.attributes["service.name"] == "my-service"
            assert provider.resource.attributes["deployment.environment.name"] == "test"
            agent = Agent(
                model=ScriptedModel(["done", "done"]),
                plugins=[OpenTelemetryTracer(tracer_provider=provider)],
            )
            with provider.get_tracer("application").start_as_current_span("api.request"):
                await asyncio.gather(agent.run("one"), agent.run("two"))
            if fail:
                raise RuntimeError("application failed")

    assert attributes["service.name"] == "overridden"
    assert trace.get_tracer_provider() is global_provider
    assert len(shutdown_threads) == 1
    assert shutdown_threads[0] != main_thread
    spans = []
    for path, headers, body in requests:
        assert path == expected_path
        assert headers["x-test"] == "harnessed"
        assert headers["Content-Type"] == "application/x-protobuf"
        data = ExportTraceServiceRequest.FromString(body)
        for resource in data.resource_spans:
            exported_attributes = {
                a.key: a.value.string_value for a in resource.resource.attributes
            }
            assert exported_attributes["service.name"] == "my-service"
            assert exported_attributes["openinference.project.name"] == "shared-agent"
            spans.extend(s for scope in resource.scope_spans for s in scope.spans)
    assert len(spans) == 7
    parent = next(s for s in spans if s.name == "api.request")
    turns = [s for s in spans if s.name == "harnessed.turn"]
    assert len(turns) == 2
    assert all(s.parent_span_id == parent.span_id for s in turns)
    assert all(s.trace_id == parent.trace_id for s in spans)


async def test_otlp_provider_shuts_down_after_task_cancellation(
    otlp_collector: Collector, shutdown_threads: list[int]
) -> None:
    base_url, requests = otlp_collector
    ready = asyncio.Event()
    main_thread = get_ident()

    async def run() -> None:
        async with otlp_provider(
            service_name="cancelled", endpoint=f"{base_url}/v1/traces"
        ) as provider:
            with provider.get_tracer("application").start_as_current_span("before.cancel"):
                pass
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(run())
    await asyncio.wait_for(ready.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert len(shutdown_threads) == 1
    assert shutdown_threads[0] != main_thread
    assert requests


async def test_otlp_provider_cleans_up_when_exporter_setup_fails(
    monkeypatch: pytest.MonkeyPatch, shutdown_threads: list[int]
) -> None:
    from harnessed.integrations import opentelemetry

    def fail_exporter(*, endpoint: str | None) -> None:
        raise RuntimeError("exporter setup failed")

    monkeypatch.setattr(opentelemetry, "OTLPSpanExporter", fail_exporter)
    main_thread = get_ident()
    with pytest.raises(RuntimeError, match="exporter setup failed"):
        async with otlp_provider(service_name="my-service"):
            pytest.fail("provider must not be yielded when setup fails")
    assert len(shutdown_threads) == 1
    assert shutdown_threads[0] != main_thread


@pytest.mark.parametrize("service_name", ["", "   "])
async def test_otlp_provider_rejects_empty_service_names(service_name: str) -> None:
    with pytest.raises(ValueError, match="service_name must not be empty"):
        async with otlp_provider(service_name=service_name):
            pytest.fail("provider must not be yielded without a service name")


def test_otlp_example_exports_on_exit(otlp_collector: Collector) -> None:
    base_url, requests = otlp_collector
    result = subprocess.run(
        [sys.executable, "-m", "examples.29_tracing", "--otlp"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=15,
        env={
            **os.environ,
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{base_url}/v1/traces",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "x-test=harnessed",
            "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT": "3",
            "OTEL_TRACES_SAMPLER": "always_on",
            "PYTHONUTF8": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "Completed tool loop" in result.stdout
    spans = [
        span
        for _, _, body in requests
        for resource in ExportTraceServiceRequest.FromString(body).resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    assert {s.name for s in spans} == {
        "backend.request",
        "harnessed.turn",
        "harnessed.step",
        "harnessed.model",
        "harnessed.tool",
        "__main__.Calculator.add",
    }
