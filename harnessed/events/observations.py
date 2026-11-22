from dataclasses import field
from enum import StrEnum
from typing import Literal

from pydantic import PositiveInt
from pydantic.dataclasses import dataclass

from harnessed.models import UserMessage
from harnessed.models.messages import SpillRef
from harnessed.models.values import VALUE_CONFIG

from .base import DurableEvent, Identifier, StepNumber
from .kinds import EventKind


class SpillExpirationReason(StrEnum):
    RETENTION = "retention"
    MISSING = "missing"


@dataclass(frozen=True, config=VALUE_CONFIG)
class ObservationRecordedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.OBSERVATION_RECORDED] = field(
        default=EventKind.OBSERVATION_RECORDED, kw_only=True
    )
    message: UserMessage
    source: Identifier
    turn_id: Identifier
    step: StepNumber
    refs: tuple[str, ...] = ()


@dataclass(frozen=True, config=VALUE_CONFIG)
class IncludedObservation:
    """One unchanged observation in the final model request, including its images."""

    source: str
    refs: tuple[str, ...]
    turn_id: Identifier = field(kw_only=True)
    step: StepNumber = field(kw_only=True)
    message_id: Identifier = field(kw_only=True)


@dataclass(frozen=True, config=VALUE_CONFIG)
class ObservationsIncludedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.OBSERVATIONS_INCLUDED] = field(
        default=EventKind.OBSERVATIONS_INCLUDED, kw_only=True
    )
    request_id: Identifier
    observations: tuple[IncludedObservation, ...]


@dataclass(frozen=True, config=VALUE_CONFIG)
class ObservationPolicyAppliedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.OBSERVATION_POLICY_APPLIED] = field(
        default=EventKind.OBSERVATION_POLICY_APPLIED, kw_only=True
    )
    max_observations: PositiveInt | None = None


@dataclass(frozen=True, config=VALUE_CONFIG)
class SpillCreatedEvent(DurableEvent):
    __hash__ = None

    type: Literal[EventKind.SPILL_CREATED] = field(default=EventKind.SPILL_CREATED, kw_only=True)
    call_id: Identifier
    ref: SpillRef
    tokens: int


@dataclass(frozen=True, config=VALUE_CONFIG)
class SpillExpiredEvent(DurableEvent):
    __hash__ = None
    type: Literal[EventKind.SPILL_EXPIRED] = field(default=EventKind.SPILL_EXPIRED, kw_only=True)
    ref: SpillRef
    reason: SpillExpirationReason = SpillExpirationReason.RETENTION
