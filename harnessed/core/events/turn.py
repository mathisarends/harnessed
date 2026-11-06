from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from harnessed.core.events.base import Identifier, SessionEvent, StepNumber
from harnessed.core.events.kinds import EventKind
from harnessed.core.limits import FinalStepPolicy
from harnessed.core.model import GenerationSettings, OutputSchema


class TurnOutcome(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    STOPPED = "stopped"
    MAX_STEPS = "max_steps"
    TOKEN_BUDGET = "token_budget"
    REJECTED = "rejected"
    ABORTED = "aborted"
    DISCARDED = "discarded"


class StepOutcome(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ABORTED = "aborted"
    CONTEXT_LIMIT = "context_limit"


@dataclass(frozen=True)
class TurnLimits:
    """Queued overrides; turn-start records store the resolved step limit."""

    max_steps: Annotated[int, Field(ge=1)] | None = None
    token_budget: Annotated[int, Field(ge=1)] | None = None

    def __post_init__(self) -> None:
        _check_limit("max_steps", self.max_steps)
        _check_limit("token_budget", self.token_budget)


def _check_limit(name: str, value: int | None) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
        raise ValueError(f"{name} must be a positive integer or None")


@dataclass(frozen=True)
class TurnStartedEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.TURN_START] = field(default=EventKind.TURN_START, kw_only=True)
    turn_id: Identifier
    input_id: str | None = None
    settings: GenerationSettings = field(default_factory=GenerationSettings)
    output_schema: OutputSchema | None = field(default=None, kw_only=True)
    limits: TurnLimits = field(default_factory=TurnLimits, kw_only=True)
    final_step: FinalStepPolicy = field(default=FinalStepPolicy.NONE, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, GenerationSettings):
            raise TypeError("Turn settings must be GenerationSettings")
        object.__setattr__(self, "final_step", FinalStepPolicy(self.final_step))


@dataclass(frozen=True)
class TurnEndedEvent(SessionEvent):
    """Durably close a turn with its outcome; the UI result is a separate event."""

    __hash__ = None

    type: Literal[EventKind.TURN_END] = field(default=EventKind.TURN_END, kw_only=True)
    turn_id: Identifier
    reason: TurnOutcome = TurnOutcome.COMPLETED

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", TurnOutcome(self.reason))


@dataclass(frozen=True)
class StepStartedEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.STEP_START] = field(default=EventKind.STEP_START, kw_only=True)
    turn_id: Identifier
    step: StepNumber


@dataclass(frozen=True)
class StepEndedEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.STEP_END] = field(default=EventKind.STEP_END, kw_only=True)
    turn_id: Identifier
    step: StepNumber
    reason: StepOutcome = StepOutcome.COMPLETED

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", StepOutcome(self.reason))
