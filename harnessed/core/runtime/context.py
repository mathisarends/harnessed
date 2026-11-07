from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from harnessed.core.events.base import DurableEvent, TransientEvent
from harnessed.core.events.messages import MessageAppendedEvent
from harnessed.core.messages import Message, UserMessage
from harnessed.core.runtime.control import SessionControl, SessionStatus
from harnessed.core.runtime.inbox import SessionInbox
from harnessed.core.session.session import SessionLog
from harnessed.core.tools.view import SessionTools

if TYPE_CHECKING:
    from harnessed.core.runtime.approvals import SessionApprovals


class PendingObservations:
    """The latest unrecorded observation per source, drained by the next step."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[UserMessage, tuple[str, ...]]] = {}

    def add(self, message: UserMessage, *, source: str, refs: Iterable[str]) -> None:
        if not isinstance(message, UserMessage):
            raise TypeError("Observations require a UserMessage")
        if not isinstance(source, str) or not source:
            raise ValueError("Observations require a non-empty source")
        references = tuple(refs)
        if any(not isinstance(ref, str) for ref in references):
            raise TypeError("Observation refs must be strings")
        self._items[source] = (message, references)

    def drain(self) -> tuple[tuple[str, UserMessage, tuple[str, ...]], ...]:
        items = tuple((source, message, refs) for source, (message, refs) in self._items.items())
        self._items.clear()
        return items


class SessionContext:
    """What plugins may do with a live session from inside its loop.

    Hook events and plugin services receive this view instead of the Session.
    It can read state, contribute context and ask for approvals, but it cannot
    start turns, mount plugins or close the session it is running in.
    """

    def __init__(
        self,
        log: SessionLog,
        control: SessionControl,
        inbox: SessionInbox,
        approvals: SessionApprovals,
        tools: SessionTools,
        observations: PendingObservations,
    ) -> None:
        self.log = log
        self.inbox = inbox
        self.approvals = approvals
        self.tools = tools
        self._control = control
        self._observations = observations

    @property
    def id(self) -> str:
        return self.log.id

    @property
    def status(self) -> SessionStatus:
        return self._control.status

    @property
    def messages(self) -> tuple[Message, ...]:
        return self.log.messages

    def last[E: DurableEvent](self, kind: type[E]) -> E | None:
        return self.log.last(kind)

    def observe(self, message: UserMessage, *, source: str, refs: Iterable[str] = ()) -> None:
        """Buffer a text/image snapshot per source for the next step.

        A newer pending snapshot replaces the same source before recording.
        Recorded snapshots remain unchanged until compaction or an explicit
        observation retention policy removes them from model history.
        """
        self._control.check_open()
        self._observations.add(message, source=source, refs=refs)

    async def append_message(self, message: UserMessage) -> None:
        """Append a user message to history without starting a turn.

        Each call appends to conversation history, including repeated messages.
        It follows ordinary history retention and may be summarized by compaction.
        A configured session store commits the contribution before this returns.
        A message ID still queued in the inbox raises ValueError without changing
        history or the queued run; use a fresh UserMessage for additive input.
        """
        self._control.check_open()
        if not isinstance(message, UserMessage):
            raise TypeError("append_message requires a UserMessage")
        await self.log.append(MessageAppendedEvent(message))
        await self.log.flush()

    async def emit(self, event: DurableEvent | TransientEvent) -> None:
        """Admit a declared execution event or publish a transient notification."""
        self._control.check_open()
        await self.log.emit(event)


__all__ = ["PendingObservations", "SessionContext"]
