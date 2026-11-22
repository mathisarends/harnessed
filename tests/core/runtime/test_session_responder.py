import asyncio

import pytest

from harnessed import Agent, Approvals, Plugin, tool
from harnessed.errors import ApprovalUnavailable
from harnessed.events import (
    ApprovalNeededEvent,
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ToolCallFinishedEvent,
)
from harnessed.kernel import hook
from harnessed.models import ToolStatus
from harnessed.testing import ScriptedModel, call


class Publish(Plugin):
    @tool
    async def publish(self) -> str:
        """Publish the draft."""
        return "Published."


async def test_answer_and_run_report_the_failed_resolution_commit():
    failure = OSError("Could not save the approval")

    async def save(commit):
        if any(isinstance(change.event, ApprovalResolvedEvent) for change in commit.changes):
            raise failure

    async with _agent().session(on_commit=save) as session, session.stream("Publish") as run:
        async with asyncio.timeout(5):
            async for event in run:
                if isinstance(event, ApprovalNeededEvent):
                    break
            assert isinstance(event, ApprovalNeededEvent)
            with pytest.raises(OSError) as answer:
                await session.approvals.allow(event.request_id)
            with pytest.raises(OSError) as result:
                await run.result()
            with pytest.raises(OSError) as retry:
                await session.approvals.allow(event.request_id)
        assert answer.value is result.value is retry.value is failure


def _agent(*, timeout_seconds: float = 300.0) -> Agent:
    return Agent(
        model=ScriptedModel([call("publish"), "done"]),
        approvals=Approvals(ask=[Publish.publish], timeout_seconds=timeout_seconds),
        plugins=[Publish()],
    )


async def _first[E](session, kind: type[E]) -> E:
    async for event in session.subscribe(transient=True):
        if isinstance(event, kind):
            return event
    raise AssertionError(f"The session closed before {kind.__name__}")


async def test_session_responder_answers_a_turn_started_by_steer():
    async with _agent().session() as session, session.approvals.responder():
        needed = asyncio.create_task(_first(session, ApprovalNeededEvent))
        await asyncio.sleep(0)
        await session.steer("Publish it.")
        async with asyncio.timeout(5):
            event = await needed
            assert [approval.request_id for approval in session.approvals.pending] == [
                event.request_id
            ]
            await session.approvals.allow(event.request_id)
            await session.wait_until_idle()
        finished = session.last(ToolCallFinishedEvent)
        assert finished is not None and finished.result.status is ToolStatus.OK


async def test_without_a_responder_allow_says_that_no_responder_held_the_request():
    errors: list[ApprovalUnavailable] = []
    sessions: list = []

    class EagerUI(Plugin):
        @hook(ApprovalRequestedEvent)
        async def answer(self, event: ApprovalRequestedEvent) -> None:
            try:
                await sessions[0].approvals.allow(event.request_id)
            except ApprovalUnavailable as error:
                errors.append(error)

    agent = Agent(
        model=ScriptedModel([call("publish"), "done"]),
        approvals=Approvals(ask=[Publish.publish]),
        plugins=[Publish(), EagerUI()],
    )
    async with agent.session() as session:
        sessions.append(session)
        await session.steer("Publish it.")
        async with asyncio.timeout(5):
            await session.wait_until_idle()
        finished = session.last(ToolCallFinishedEvent)
        assert finished is not None and finished.result.status is ToolStatus.UNAVAILABLE
    (error,) = errors
    assert "No live responder held this approval" in str(error)


async def test_a_closing_stream_hands_its_question_to_the_session_responder():
    async with _agent().session() as session, session.approvals.responder():
        async with asyncio.timeout(5):
            async with session.stream("Publish it.") as run:
                async for event in run:
                    if isinstance(event, ApprovalNeededEvent):
                        request_id = event.request_id
                        break
            assert [approval.request_id for approval in session.approvals.pending] == [request_id]
            await session.approvals.allow(request_id)
            await session.wait_until_idle()
        finished = session.last(ToolCallFinishedEvent)
        assert finished is not None and finished.result.status is ToolStatus.OK


async def test_a_stream_keeps_answering_its_own_turn_while_a_responder_is_open():
    announced: list[str] = []
    async with _agent().session() as session, session.approvals.responder():
        async with asyncio.timeout(5):
            async with session.stream("Publish it.") as run:
                async for event in run:
                    if isinstance(event, ApprovalNeededEvent):
                        announced.append(event.request_id)
                        await session.approvals.deny(event.request_id)
                result = await run.result()
        assert len(announced) == 1
        assert result.tool_calls[0][1].status is ToolStatus.DENIED


async def test_unanswered_questions_expire_and_closing_the_responder_ends_held_ones():
    async with _agent(timeout_seconds=0.05).session() as session:
        async with session.approvals.responder():
            await session.steer("Publish it.")
            async with asyncio.timeout(5):
                await session.wait_until_idle()
        finished = session.last(ToolCallFinishedEvent)
        assert finished is not None and finished.result.status is ToolStatus.UNAVAILABLE

    async with _agent().session() as session:
        async with session.approvals.responder():
            needed = asyncio.create_task(_first(session, ApprovalNeededEvent))
            await asyncio.sleep(0)
            await session.steer("Publish it.")
            async with asyncio.timeout(5):
                await needed
        async with asyncio.timeout(5):
            await session.wait_until_idle()
        finished = session.last(ToolCallFinishedEvent)
        assert finished is not None and finished.result.status is ToolStatus.UNAVAILABLE
