import json

import pytest

from harnessed import Agent, Approvals, AutoReview, Decision, Plugin, tool
from harnessed.approvals import ApprovalReviewRequest
from harnessed.events import ApprovalRequestedEvent, ApprovalResolvedEvent
from harnessed.models import (
    ApprovalResponse,
    ApprovalSource,
    ModelChunk,
    ToolCall,
    ToolMessage,
    ToolStatus,
    UserMessage,
)
from harnessed.sessions import HistoryEntry
from harnessed.testing import ScriptedModel, call


class Documents(Plugin):
    @tool(readonly=True)
    def inspect_revision(self, revision: str) -> str:
        return f"Checked {revision}"

    @tool
    def publish_revision(self, revision: str) -> str:
        return f"Published {revision}"


async def test_auto_review_receives_the_pending_call_and_retained_conversation():
    review_model = ScriptedModel(
        [
            call("final_answer", decision="allow", reason="The user authorized revision 42."),
        ]
    )
    inspected = ToolCall("inspect_revision", {"revision": "42"}, id="inspection")
    pending = ToolCall("publish_revision", {"revision": "42"}, id="publication")
    agent = Agent(
        model=ScriptedModel(
            [
                [ModelChunk(text="Checking it.", reasoning="private thought", tool_call=inspected)],
                "I found it.",
                [ModelChunk(tool_call=pending)],
                "Done.",
            ]
        ),
        instructions="private worker instructions",
        approvals=Approvals(
            ask=[Documents.publish_revision],
            reviewer=AutoReview(model=review_model, instructions="Require explicit permission."),
        ),
        plugins=[Documents()],
    )
    async with agent.session() as session:
        await session.run("You can publish revision 42 once checked.")
        result = await session.run("Publish that revision.")

    assert result.tool_calls[0][1].status is ToolStatus.OK
    request = review_model.requests[0]
    evidence = json.loads(request.messages[-1].text)
    assert evidence["action"]["id"] == pending.id
    assert evidence["action"]["arguments"] == {"revision": "42"}
    transcript = evidence["context"]
    assert "You can publish revision 42 once checked." in transcript
    assert "Publish that revision." in transcript
    assert "Checking it." in transcript
    assert 'inspect_revision [inspection] {"revision": "42"}' in transcript
    assert "result [inspection] (ok) Checked 42" in transcript
    assert "private thought" not in transcript
    assert "private worker instructions" not in transcript
    instructions = request.messages[0].text
    assert "Require explicit permission." in instructions
    assert "You can publish revision" not in instructions
    resolved = next(event for event in result.events if isinstance(event, ApprovalResolvedEvent))
    assert resolved.source is ApprovalSource.REVIEWER
    assert resolved.reason == "The user authorized revision 42."


@pytest.mark.parametrize(
    ("approver_answers", "status"),
    [(True, ToolStatus.OK), (False, ToolStatus.UNAVAILABLE)],
)
async def test_auto_review_can_defer_to_an_approver(approver_answers, status):
    review_model = ScriptedModel(
        [
            call("final_answer", decision="ask", reason="A person must confirm."),
        ]
    )

    def approve(request: ApprovalRequestedEvent) -> ApprovalResponse:
        return request.allow()

    worker_model = ScriptedModel([call(Documents.publish_revision, revision="42"), "Done."])
    agent = Agent(
        model=worker_model,
        approvals=Approvals(
            ask=[Documents.publish_revision],
            reviewer=AutoReview(model=review_model, instructions="Escalate uncertain requests."),
            approver=approve if approver_answers else None,
        ),
        plugins=[Documents()],
    )
    result = await agent.run("Publish revision 42.")
    assert result.tool_calls[0][1].status is status
    resolved = next(event for event in result.events if isinstance(event, ApprovalResolvedEvent))
    assert resolved.source is (ApprovalSource.USER if approver_answers else None)


async def test_policy_denial_bypasses_a_configured_reviewer():
    review_model = ScriptedModel([])
    worker_model = ScriptedModel([call(Documents.publish_revision, revision="42"), "Blocked."])
    agent = Agent(
        model=worker_model,
        approvals=Approvals(
            deny=[Documents.publish_revision],
            reviewer=AutoReview(model=review_model, instructions="Review publication."),
        ),
        plugins=[Documents()],
    )
    result = await agent.run("Publish revision 42.")
    assert result.tool_calls[0][1].status is ToolStatus.DENIED
    assert review_model.requests == []


async def test_context_callback_can_transform_and_extend_the_transcript():
    review_model = ScriptedModel(
        [
            call(
                "final_answer", decision=Decision.DENY.value, reason="Outside the release window."
            ),
        ]
    )

    def context(review: ApprovalReviewRequest, transcript: str) -> str:
        assert review.approval.call.name == "publish_revision"
        return transcript.replace("sensitive detail", "[redacted]") + "\nRelease window closed."

    worker_model = ScriptedModel([call(Documents.publish_revision, revision="42"), "Blocked."])
    agent = Agent(
        model=worker_model,
        approvals=Approvals(
            ask=[Documents.publish_revision],
            reviewer=AutoReview(
                model=review_model, instructions="Check the release window.", context=context
            ),
        ),
        plugins=[Documents()],
    )
    result = await agent.run("Publish revision 42 with sensitive detail.")
    assert result.tool_calls[0][1].status is ToolStatus.DENIED
    evidence = json.loads(review_model.requests[0].messages[-1].text)
    assert (
        evidence["context"] == "user: Publish revision 42 with [redacted].\nRelease window closed."
    )


async def test_default_transcript_prioritizes_user_inputs_and_bounds_tool_output():
    review_model = ScriptedModel(
        [
            call("final_answer", decision="deny", reason="The authorizations do not match."),
        ]
    )
    reviewer = AutoReview(model=review_model, instructions="Obey the authorized revision.")
    approval = ApprovalRequestedEvent("review", ToolCall("publish"), "Publish", "tool-policy")
    history = [
        HistoryEntry(0, UserMessage("Only revision 42 is authorized."), is_input=True),
        HistoryEntry(1, UserMessage("Synthetic observation")),
    ]
    for index in range(2, 30):
        output = ToolMessage(f"Evidence {index} " + "x" * 10_000, tool_call_id=f"call-{index}")
        history.append(HistoryEntry(index, output))
    history.append(HistoryEntry(30, UserMessage("Publish revision 43."), is_input=True))
    await reviewer.review(ApprovalReviewRequest(approval, tuple(history)))

    evidence = json.loads(review_model.requests[0].messages[-1].text)
    transcript = evidence["context"]
    assert "Only revision 42 is authorized." in transcript
    assert "Publish revision 43." in transcript
    assert "Evidence 29" in transcript
    assert "Evidence 2 " not in transcript
    assert "Synthetic observation" not in transcript
    assert "Some conversation entries were omitted." in transcript
    assert len(transcript) < 9_000
