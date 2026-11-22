import random

import pytest

from harnessed.approvals.policy import ApprovalState
from harnessed.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
    AssistantMessageEvent,
    ControlState,
    InboxAddedEvent,
    InboxMode,
    InboxRemovedEvent,
    ModelRequestedEvent,
    SessionControlChangedEvent,
    StepEndedEvent,
    StepStartedEvent,
    TurnEndedEvent,
    TurnStartedEvent,
    UserMessageEvent,
)
from harnessed.models import (
    AssistantMessage,
    Decision,
    ModelInfo,
    ModelRequest,
    ToolCall,
    UserMessage,
)
from harnessed.sessions.control import RunControl
from harnessed.sessions.inbox import Inbox
from harnessed.sessions.log import SessionLog, derive_messages, derive_requests
from tests._audit import derive_turns


@pytest.mark.parametrize("seed", range(12))
async def test_incremental_snapshots_equal_full_folds_for_generated_valid_logs(seed):
    rng = random.Random(seed)
    session = SessionLog(f"generated-{seed}")
    prefix = []
    snapshots = []

    async def append(event):
        await session.append(event)
        prefix.append(event)
        assert session.inbox == Inbox(prefix)
        assert session.approvals.pending == ApprovalState(prefix).pending
        assert session.control == RunControl(prefix)
        assert session.turns == derive_turns(session.events)
        assert session.requests == derive_requests(session.events)
        assert session.messages == derive_messages(prefix)
        assert session.messages is session.messages
        assert session.events is session.events
        snapshots.append(
            (tuple(prefix), Inbox(prefix), ApprovalState(prefix), derive_turns(prefix))
        )

    for turn in range(5):
        message = UserMessage(f"Task {turn}", id=f"input-{turn}")
        await append(InboxAddedEvent.create(message, InboxMode.FOLLOWUP))
        await append(TurnStartedEvent(str(turn), message.id))
        await append(UserMessageEvent(message))
        for step in range(1, rng.randint(2, 5)):
            await append(StepStartedEvent(str(turn), step))
            await append(
                ModelRequestedEvent(
                    ModelRequest(session.messages), request_id="request", model=ModelInfo("test")
                )
            )
            call = ToolCall(f"tool-{rng.randrange(3)}", id=f"call-{turn}-{step}")
            request = ApprovalRequestedEvent(f"approval-{turn}-{step}", call, "test", "generated")
            await append(request)
            answer = rng.choice([Decision.ALLOW, Decision.DENY])
            remember = answer is Decision.ALLOW and rng.choice([True, False])
            await append(ApprovalResolvedEvent(request.request_id, answer, remember=remember))
            assert session.latest_approval(call.id).decision == answer
            if remember and rng.choice([True, False]):
                await append(ApprovalRevokedEvent(request.request_id))
            await append(AssistantMessageEvent(AssistantMessage("reply"), request_id="request"))
            await append(StepEndedEvent(str(turn), step))
        await append(TurnEndedEvent(str(turn)))
        await append(
            SessionControlChangedEvent(
                rng.choice([ControlState.ACTIVE, ControlState.PAUSED, ControlState.STOPPED])
            )
        )

    for events, inbox, approvals, turns in snapshots:
        assert inbox == Inbox(events) and approvals == ApprovalState(events)
        assert turns == derive_turns(events)


async def test_projection_validation_failure_does_not_advance_log_or_state():
    session = SessionLog("invalid-transition")
    original = session.events, session.inbox, session.approvals, session.control
    with pytest.raises(ValueError, match="unknown inbox"):
        await session.append(InboxRemovedEvent("absent"))
    assert (session.events, session.inbox, session.approvals, session.control) == original
    assert session.last(InboxRemovedEvent) is None


async def test_last_and_since_indexes_match_the_log_suffix():
    session = SessionLog("indexes")
    assert session.last(TurnStartedEvent) is None and session.since(TurnStartedEvent) == ()
    start = TurnStartedEvent("turn")
    message = UserMessageEvent(UserMessage("hello"))
    await session.append(start)
    before = session.events
    await session.append(message)
    assert session.last(TurnStartedEvent) is start and session.since(TurnStartedEvent) == (message,)
    assert before == (start,)
