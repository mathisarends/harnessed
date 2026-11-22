from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from harnessed.approvals.responders import Approver
from harnessed.events import SubagentLinkedEvent
from harnessed.sessions import CommitHandler

if TYPE_CHECKING:
    from harnessed.runtime.session import Session


class ChildStatusReporter(Protocol):
    async def answer(self, request_id: str, text: str) -> None:
        """Acknowledge one pending request without completing the child's task."""
        ...


@dataclass(frozen=True)
class ChildLimits:
    max_parallel: int
    max_calls: int
    max_depth: int
    timeout_seconds: float
    max_steps: int
    token_budget: int | None


@dataclass(frozen=True)
class ChildSessionRequest:
    """Application boundary for opening an isolated, parent-owned session.

    ``on_commit`` is the parent's commit handler, so one application handler
    receives the commits of a whole delegation tree under the parent's
    ``commit_timeout_seconds``.
    """

    session_id: str
    link: SubagentLinkedEvent
    approver: Approver
    reporter: ChildStatusReporter | None = None
    on_commit: CommitHandler | None = None
    commit_timeout_seconds: float | None = None


@runtime_checkable
class ChildSessionFactory(Protocol):
    def child_session(self, request: ChildSessionRequest) -> AbstractAsyncContextManager[Session]:
        """Open a fresh child with its own services, log and plugin lifetimes."""
        ...
