from dataclasses import field
from enum import StrEnum
from typing import Literal

from pydantic import PositiveInt
from pydantic.dataclasses import dataclass

from harnessed.models import GenerationSettings, ModelUsage, OutputSchema
from harnessed.models.values import VALUE_CONFIG

from .base import DurableEvent, Identifier, StepNumber
from .kinds import EventKind


class FinalStepPolicy(StrEnum):
    NONE = "none"
    SUMMARIZE = "summarize"


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


@dataclass(frozen=True, config=VALUE_CONFIG)
class TurnLimits:
    """Queued overrides; turn-start records store the resolved step limit."""

    max_steps: PositiveInt | None = None
    token_budget: PositiveInt | None = None


@dataclass(frozen=True, config=VALUE_CONFIG)
class TurnStartedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.TURN_STARTED] = field(default=EventKind.TURN_STARTED, kw_only=True)
    turn_id: Identifier
    input_id: str | None = None
    settings: GenerationSettings = field(default_factory=GenerationSettings)
    output_schema: OutputSchema | None = field(default=None, kw_only=True)
    limits: TurnLimits = field(default_factory=TurnLimits, kw_only=True)
    final_step: FinalStepPolicy = field(default=FinalStepPolicy.NONE, kw_only=True)


@dataclass(frozen=True, config=VALUE_CONFIG)
class TurnEndedEvent(DurableEvent):
    """Close a turn with its outcome and aggregate provider-reported usage."""

    __hash__ = None

    type: Literal[EventKind.TURN_ENDED] = field(default=EventKind.TURN_ENDED, kw_only=True)
    turn_id: Identifier
    reason: TurnOutcome = TurnOutcome.COMPLETED
    usage: ModelUsage = field(default_factory=lambda: ModelUsage(0), kw_only=True)


@dataclass(frozen=True, config=VALUE_CONFIG)
class StepStartedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.STEP_STARTED] = field(default=EventKind.STEP_STARTED, kw_only=True)
    turn_id: Identifier
    step: StepNumber


@dataclass(frozen=True, config=VALUE_CONFIG)
class StepEndedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.STEP_ENDED] = field(default=EventKind.STEP_ENDED, kw_only=True)
    turn_id: Identifier
    step: StepNumber
    reason: StepOutcome = StepOutcome.COMPLETED
