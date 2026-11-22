import asyncio

from harnessed import Agent, Approvals, Decision, Plugin, tool
from harnessed.hooks import (
    ExecuteTool,
    Next,
    OutputDecision,
    PrepareOutput,
    PrepareRequest,
    PreStep,
    ReviewToolResult,
    SessionClosing,
    SessionOpened,
    StepDecision,
    ToolPolicy,
    TurnStopping,
)
from harnessed.kernel import hook, needs
from harnessed.models import Decision as ToolDecision
from harnessed.models import ModelChunk, ModelRequest, ToolCall, ToolResult
from harnessed.plugins import Subagents
from harnessed.runtime.hooks import SessionAppended
from harnessed.sessions import SessionContext
from harnessed.testing import CallbackModel, ScriptedModel

_BACKGROUND_DEADLINE_SECONDS = 5.0


class Witness(Plugin):
    """Record every hook whose session differs from the activation's own."""

    _session = needs(SessionContext)

    def __init__(self, seen: set[str], foreign: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self._seen = seen
        self._foreign = foreign

    def _check(self, hook_name: str, session_id: str) -> None:
        self._seen.add(self._session.id)
        if session_id != self._session.id:
            self._foreign.append((hook_name, self._session.id, session_id))

    @tool(readonly=True)
    async def inspect(self) -> str:
        return "Inspected"

    @hook(SessionOpened)
    def _opened(self, event: SessionOpened) -> None:
        self._check("SessionOpened", event.session.id)

    @hook(SessionClosing)
    def _closing(self, event: SessionClosing) -> None:
        self._check("SessionClosing", event.session.id)

    @hook(SessionAppended)
    def _appended(self, event: SessionAppended) -> None:
        self._check("SessionAppended", event.log.id)

    @hook(PreStep)
    async def _step(self, event: PreStep, next_: Next[StepDecision]) -> StepDecision:
        self._check("PreStep", event.session.id)
        return await next_()

    @hook(PrepareRequest)
    async def _request(self, event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
        self._check("PrepareRequest", event.session.id)
        return await next_()

    @hook(PrepareOutput)
    async def _output(self, event: PrepareOutput, next_: Next[OutputDecision]) -> OutputDecision:
        self._check("PrepareOutput", event.session.id)
        return await next_()

    @hook(ToolPolicy)
    async def _policy(self, event: ToolPolicy, next_: Next[ToolDecision]) -> ToolDecision:
        self._check("ToolPolicy", event.session.id)
        return await next_()

    @hook(ExecuteTool)
    async def _execute(self, event: ExecuteTool, next_: Next[ToolResult]) -> ToolResult:
        self._check("ExecuteTool", event.session.id)
        return await next_()

    @hook(ReviewToolResult)
    async def _review(self, event: ReviewToolResult, next_: Next[ToolResult]) -> ToolResult:
        self._check("ReviewToolResult", event.session.id)
        return await next_()

    @hook(TurnStopping)
    def _stopping(self, event: TurnStopping) -> None:
        self._check("TurnStopping", event.session.id)


def _child_agent(witness: Witness) -> Agent:
    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("inspect"))], "Child answer"])
    return Agent(
        model=model,
        approvals=Approvals(default=Decision.ALLOW),
        plugins=[witness],
    )


async def test_hooks_of_a_delegated_child_never_reach_the_parent_activation():
    seen: set[str] = set()
    foreign: list[tuple[str, str, str]] = []
    witness = Witness(seen, foreign)
    call = ToolCall("delegate", {"agent": "review", "task": "Review"})
    parent = Agent(
        model=ScriptedModel([[ModelChunk(tool_call=call)], "Parent answer"]),
        approvals=Approvals(default=Decision.ALLOW),
        plugins=[Subagents({"review": _child_agent(witness)}), witness],
    )

    async with parent.session() as session:
        await session.run("Delegate")
        child_id = session.children[0].id
        parent_id = session.id

    assert seen == {parent_id, child_id}
    assert foreign == []


async def test_hooks_of_a_background_child_never_reach_the_parent_activation():
    seen: set[str] = set()
    foreign: list[tuple[str, str, str]] = []
    witness = Witness(seen, foreign)
    notified = asyncio.Event()
    responses = 0

    async def parent_response(_request: ModelRequest):
        nonlocal responses
        responses += 1
        if responses == 1:
            call = ToolCall("spawn", {"agent": "review", "task": "Review"})
            yield ModelChunk(tool_call=call)
        elif responses == 2:
            yield ModelChunk(text="Working independently")
        else:
            notified.set()
            yield ModelChunk(text="Handled result")

    parent = Agent(
        model=CallbackModel(parent_response),
        approvals=Approvals(default=Decision.ALLOW),
        plugins=[Subagents({"review": _child_agent(witness)}), witness],
    )

    async with parent.session() as session:
        await session.run("Start")
        async with asyncio.timeout(_BACKGROUND_DEADLINE_SECONDS):
            await notified.wait()
            await session.wait_until_idle()
        child_id = session.children[0].id
        parent_id = session.id

    assert seen == {parent_id, child_id}
    assert foreign == []
