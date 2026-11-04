from typing import assert_type

from harnessed import Agent, Session
from harnessed.events import SubagentLinkedEvent, SubagentStatus
from harnessed.models import ModelUsage
from harnessed.plugins import Subagents
from harnessed.plugins.subagents import (
    ChildActivity,
    ChildReport,
    ChildRuntimeState,
    ChildSession,
    ChildSessionFactory,
    ChildSessionRequest,
)


def configure(agent: Agent) -> Subagents:
    factory: ChildSessionFactory = agent
    return Subagents({"review": factory}, max_parallel=2, token_budget=10_000)


async def inspect(
    session: Session, factory: ChildSessionFactory, request: ChildSessionRequest
) -> None:
    assert_type(session.children, tuple[ChildSession, ...])
    assert_type(session.parent, SubagentLinkedEvent | None)
    assert_type(session.tree_usage, ModelUsage)
    for child in session.children:
        assert_type(child.status, SubagentStatus)
        assert_type(child.runtime, ChildRuntimeState)
        assert_type(child.runtime.activity, ChildActivity)
        assert_type(child.runtime.report, ChildReport | None)
    async with factory.child_session(request) as child:
        assert_type(child, Session)
