import inspect
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Self

import pytest

from harnessed import Agent, ApprovalAnswer, Plugin, ToolCall
from harnessed.core.model import ModelChunk
from harnessed.core.tools.decorator import tool
from harnessed.core.tracing import Tracer
from harnessed.hooks import PreStep, SessionClosing, StepDecision
from harnessed.kernel import Next, PluginScope, Scope, hook
from harnessed.plugins import Approvals, Compaction, every_turns
from harnessed.services import Summary
from harnessed.testing import ScriptedModel
from harnessed.tracing import current, record, span, traced


class Recorder(Plugin, slot="tracer"):
    def __init__(self):
        super().__init__()
        self.active = []
        self.finished = []

    def create_instance(self) -> Self:
        instance = super().create_instance()
        instance.active = self.active
        instance.finished = self.finished
        return instance

    async def setup(self, scope: PluginScope) -> None:
        scope.provide(Tracer, self)

    @contextmanager
    def span(self, name, **attributes):
        self.active.append(name)
        try:
            yield self
        finally:
            self.finished.append((name, dict(attributes)))
            assert self.active.pop() == name

    def set_attribute(self, name, value):
        pass


async def test_tool_span_starts_after_approval_wait_and_all_spans_close():
    trace = Recorder()

    class Work(Plugin):
        @tool
        async def work(self) -> str:
            assert trace.active == ["harnessed.turn", "harnessed.step", "harnessed.tool"]
            return "ready"

    def approve(request):
        assert trace.active == ["harnessed.turn", "harnessed.step", "harnessed.approval_wait"]
        return ApprovalAnswer.ALLOW

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("work"))], "done"])
    await Agent(model=model, plugins=[Work(), Approvals(approver=approve), trace]).run("Go")
    names = [name for name, _ in trace.finished]
    assert names.index("harnessed.approval_wait") < names.index("harnessed.tool")
    assert names.count("harnessed.model") == names.count("harnessed.step") == 2
    assert names[-1] == "harnessed.turn" and trace.active == []


async def test_failed_model_closes_every_span():
    trace = Recorder()
    with pytest.raises(RuntimeError, match="ran out"):
        await Agent(model=ScriptedModel([]), plugins=[trace]).run("Go")
    assert trace.active == []
    assert [name for name, _ in trace.finished] == [
        "harnessed.model",
        "harnessed.step",
        "harnessed.turn",
    ]


async def test_explicit_tracing_owner_overrides_ambient_context_and_restores_after_error():
    outer = Recorder()
    replacement = Recorder()
    previous = current()
    async with Scope() as owner, Scope() as untraced:
        owner.provide(Tracer, outer)
        with pytest.raises(RuntimeError, match="source failure"):
            with span("library.outer", scope=owner):
                assert current() is outer
                owner.provide(Tracer, replacement)
                with span("library.child"):
                    assert current() is outer
                with span("library.suppressed", scope=untraced):
                    assert current() is not outer
                    with span("library.also_suppressed"):
                        pass
                assert current() is outer
                raise RuntimeError("source failure")
        assert current() is previous
        with span("library.replacement", scope=owner):
            assert current() is replacement
    assert [name for name, _ in outer.finished] == ["library.child", "library.outer"]
    assert [name for name, _ in replacement.finished] == ["library.replacement"]
    assert current() is previous


async def test_traced_preserves_sync_async_signatures_and_works_without_a_tracer():
    def source(value: int, *, offset: int = 1) -> int:
        """Add an offset."""
        record(value=value)
        return value + offset

    async def async_source(value: str, *, suffix: str = "!") -> str:
        record(length=len(value))
        return value + suffix

    synchronous = traced(source)
    asynchronous = traced(async_source)
    assert synchronous(4, offset=3) == 7
    assert await asynchronous("ready", suffix=".") == "ready."
    assert synchronous.__name__ == source.__name__
    assert synchronous.__doc__ == source.__doc__
    assert inspect.unwrap(synchronous) is source
    assert inspect.unwrap(asynchronous) is async_source
    assert inspect.signature(synchronous) == inspect.signature(source)
    assert inspect.signature(asynchronous) == inspect.signature(async_source)
    assert inspect.iscoroutinefunction(asynchronous)
    assert not inspect.iscoroutinefunction(synchronous)
    record(outside=True)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_traced_rejects_generators_instead_of_timing_their_creation(asynchronous: bool) -> None:
    def source() -> Iterator[int]:
        yield 1

    async def async_source() -> AsyncIterator[int]:
        yield 1

    with pytest.raises(TypeError, match="inside generator functions"):
        traced(async_source if asynchronous else source)


async def test_traced_preserves_instance_class_and_static_method_binding():
    class Operations:
        @traced
        def increment(self, value: int) -> int:
            return value + 1

        @classmethod
        @traced
        async def name(cls, *, prefix: str) -> str:
            return prefix + cls.__name__

        @staticmethod
        @traced
        def double(value: int) -> int:
            return value * 2

    assert Operations().increment(3) == 4
    assert await Operations.name(prefix="type:") == "type:Operations"
    assert Operations.double(3) == 6


async def test_library_tracing_is_available_in_step_and_closing_hooks():
    trace = Recorder()

    class Observe(Plugin):
        @hook(PreStep)
        async def prepare(self, event: PreStep, next_: Next[StepDecision]) -> StepDecision:
            assert isinstance(current(), Recorder)
            with span("library.prepare"):
                return await next_()

        @hook(SessionClosing)
        async def closing(self, event: SessionClosing) -> None:
            assert isinstance(current(), Recorder)
            with span("library.cleanup"):
                pass

    result = await Agent(model=ScriptedModel(["done"]), plugins=[trace, Observe()]).run("go")
    assert result.text == "done"
    assert [name for name, _ in trace.finished] == [
        "library.prepare",
        "harnessed.model",
        "harnessed.step",
        "harnessed.turn",
        "library.cleanup",
    ]
    assert trace.active == []


async def test_ineffective_compaction_backs_off_and_has_its_own_span(caplog):
    class Ineffective:
        async def summarize(self, request):
            return Summary("long summary " * 10_000)

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            return "small"

    trace = Recorder()
    model = ScriptedModel(
        [
            "short",
            [ModelChunk(tool_call=ToolCall("work"))],
            [ModelChunk(tool_call=ToolCall("work"))],
            "done",
            "third",
            "fourth",
        ]
    )
    app = Agent(
        model=model,
        plugins=[
            trace,
            Work(),
            Compaction(
                trigger=every_turns(1),
                summarizer=Ineffective(),
                keep_last_steps=1,
                max_summary_tokens=40_000,
            ),
        ],
    )
    async with app.session() as session:
        await session.run("first")
        await session.run("second")
        assert [name for name, _ in trace.finished].count("harnessed.compaction") == 1
        await session.run("third")
        assert [name for name, _ in trace.finished].count("harnessed.compaction") == 1
        await session.run("fourth")
        assert [name for name, _ in trace.finished].count("harnessed.compaction") == 2
    assert "did not reduce context" in caplog.text
