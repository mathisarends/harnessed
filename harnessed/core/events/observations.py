from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from harnessed.core.events.base import DurableEvent, Identifier, StepNumber
from harnessed.core.events.kinds import EventKind
from harnessed.core.messages import SpillRef, UserMessage


class SpillExpirationReason(StrEnum):
    RETENTION = "retention"
    MISSING = "missing"


@dataclass(frozen=True)
class ObservationRecordedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.OBSERVATION_RECORDED] = field(
        default=EventKind.OBSERVATION_RECORDED, kw_only=True
    )
    message: UserMessage
    source: str
    turn_id: Identifier
    step: StepNumber
    refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.message, UserMessage):
            raise TypeError("Observations require a user message")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("Observations require a non-empty source")
        object.__setattr__(self, "refs", tuple(self.refs))
        if any(not isinstance(ref, str) for ref in self.refs):
            raise TypeError("Observation refs must be strings")


@dataclass(frozen=True)
class IncludedObservation:
    """One unchanged observation in the final model request, including its images."""

    source: str
    refs: tuple[str, ...]
    turn_id: Identifier = field(kw_only=True)
    step: StepNumber = field(kw_only=True)
    message_id: Identifier = field(kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "refs", tuple(self.refs))


@dataclass(frozen=True)
class ObservationsIncludedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.OBSERVATIONS_INCLUDED] = field(
        default=EventKind.OBSERVATIONS_INCLUDED, kw_only=True
    )
    request_id: Identifier
    observations: tuple[IncludedObservation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))


@dataclass(frozen=True)
class ObservationPolicyAppliedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.OBSERVATION_POLICY_APPLIED] = field(
        default=EventKind.OBSERVATION_POLICY_APPLIED, kw_only=True
    )
    max_observations: int | None = None

    def __post_init__(self) -> None:
        if self.max_observations is not None and (
            type(self.max_observations) is not int or self.max_observations < 1
        ):
            raise ValueError("max_observations must be a positive integer or None")


@dataclass(frozen=True)
class SpillCreatedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.SPILL_CREATED] = field(default=EventKind.SPILL_CREATED, kw_only=True)
    call_id: Identifier
    ref: SpillRef
    tokens: int


@dataclass(frozen=True)
class SpillExpiredEvent(DurableEvent):
    __hash__ = None
    type: Literal[EventKind.SPILL_EXPIRED] = field(default=EventKind.SPILL_EXPIRED, kw_only=True)
    ref: SpillRef
    reason: SpillExpirationReason = SpillExpirationReason.RETENTION
