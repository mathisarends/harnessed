from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from harnessed.events import ApprovalRequestedEvent, DurableEvent, UserMessageEvent
from harnessed.kernel import Bail, Emit, Waterfall
from harnessed.kernel import Next as Next
from harnessed.models import (
    Decision,
    Message,
    Model,
    ModelRequest,
    ModelStream,
    OutputSchema,
    Role,
    ToolCall,
    ToolResult,
)
from harnessed.models.messages import ApprovalResponse
from harnessed.tools import OutputLimit, OutputLimitSetting, ToolEnvelope, ToolInfo

if TYPE_CHECKING:
    from harnessed.sessions import SessionContext
    from harnessed.sessions.log import SessionLog


@dataclass(frozen=True)
class StepDecision:
    messages: tuple[Message, ...] = ()
    accepted: bool = True

    @classmethod
    def reject(cls) -> Self:
        """End this turn and permanently remove its pending step inputs."""
        return cls(accepted=False)


@dataclass(frozen=True)
class PreStep(Waterfall[StepDecision]):
    session: SessionContext
    messages: tuple[Message, ...]
    step: int
    max_steps: int = field(kw_only=True)
    is_final_step: bool = field(default=False, kw_only=True)

    @property
    def remaining_steps(self) -> int:
        """Remaining step allowance, including the step about to start."""
        return max(0, self.max_steps - self.step + 1)

    @property
    def is_last_step(self) -> bool:
        return self.remaining_steps == 1

    @property
    def is_turn_start(self) -> bool:
        return self.step == 1

    @property
    def latest_user_text(self) -> str:
        for message in reversed(self.messages):
            if message.role == Role.USER:
                return message.text
        event = self.session.log.last(UserMessageEvent)
        return event.message.text if event is not None else ""


@dataclass(frozen=True)
class ExecuteTool(Waterfall[ToolResult]):
    call: ToolCall
    session: SessionContext
    tool: ToolInfo | None = field(default=None, kw_only=True)
    envelope: ToolEnvelope = field(default=ToolEnvelope(), kw_only=True)


@dataclass(frozen=True)
class ToolPolicy(Waterfall[Decision]):
    call: ToolCall
    session: SessionContext
    tool: ToolInfo | None = field(default=None, kw_only=True)
    envelope: ToolEnvelope = field(default=ToolEnvelope(), kw_only=True)


@dataclass(frozen=True)
class ReviewToolResult(Waterfall[ToolResult]):
    call: ToolCall
    result: ToolResult
    session: SessionContext
    output_limit: OutputLimitSetting = field(default=OutputLimit.INHERIT, kw_only=True)
    tool: ToolInfo | None = field(default=None, kw_only=True)
    envelope: ToolEnvelope = field(default=ToolEnvelope(), kw_only=True)


@dataclass(frozen=True)
class PrepareRequest(Waterfall[ModelRequest]):
    """Transform an assembled request before output constraints and token checks.

    Compose with the request returned by next_(); request is the original input
    to this hook chain, rather than another hook's returned value.
    """

    session: SessionContext
    request: ModelRequest


class OutputDecision(StrEnum):
    ENFORCE = "enforce"
    DEFER = "defer"


@dataclass(frozen=True)
class PrepareOutput(Waterfall[OutputDecision]):
    """Decide whether this step enforces the turn's declared output contract.

    Dispatched after PrepareRequest and before native output preparation, token
    checks and model dispatch. DEFER permits ordinary text without consuming
    output correction attempts. The declared turn contract remains unchanged.
    Reserved final-summary steps bypass this hook and remain unstructured.
    """

    session: SessionContext
    request: ModelRequest
    schema: OutputSchema | None


@dataclass(frozen=True)
class StreamModel(Waterfall[AbstractAsyncContextManager[ModelStream]]):
    """Wrap or replace one agent-loop stream after request admission and resolution.

    Return an unentered context manager; the runtime owns entry, consumption and
    exit. next_() returns the downstream manager, defaulting to model.stream(request).
    Place @hook(StreamModel) above @asynccontextmanager for a Model.stream-style
    method that yields the stream directly.
    The request contains resolved assets and effective settings. Request transforms
    belong in PrepareRequest so audit, output constraints and budgets see them.
    Standalone model calls and compaction requests do not dispatch this hook.
    """

    session: SessionContext
    request: ModelRequest
    model: Model
    request_id: str = field(kw_only=True)


@dataclass(frozen=True)
class TurnStopping(Bail[bool]):
    session: SessionContext


@dataclass(frozen=True)
class RequestApproval(Bail[ApprovalResponse | Decision]):
    request: ApprovalRequestedEvent
    session: SessionContext

    @property
    def call(self) -> ToolCall:
        return self.request.call

    @property
    def reason(self) -> str:
        return self.request.reason


@dataclass(frozen=True)
class SessionAppended(Emit):
    """Dispatched after an event is appended to the session log, before its own hooks."""

    log: SessionLog
    event: DurableEvent
    seq: int


@dataclass(frozen=True)
class SessionAborting(Emit):
    """Drain session-owned background work before interruption repairs the log."""

    log: SessionLog


@dataclass(frozen=True)
class SessionOpened(Emit):
    """Observe the validated session before queued work starts."""

    session: SessionContext
    at: float  # time.monotonic() at dispatch, shared by all listeners.


@dataclass(frozen=True)
class SessionClosing(Emit):
    """Release live resources after driver shutdown, before plugin disposal.

    Explicit Session.aclose() dispatches this once. Plugins must also own cleanup
    through Scope effects for startup rollback and context teardown.
    """

    session: SessionContext
