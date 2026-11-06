from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from harnessed.core.diagnostics import (
    ModelAttemptFinished,
    ModelAttemptStarted,
    ModelRetryScheduled,
    RetryKind,
)
from harnessed.core.events.base import Identifier, SessionEvent, TransientEvent
from harnessed.core.events.kinds import EventKind
from harnessed.core.model import ModelInfo, ModelRequest, ModelUsage, UsagePurpose


class ModelRequestOutcome(StrEnum):
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONTEXT_EXCEEDED = "context_exceeded"


@dataclass(frozen=True)
class ModelRequestedEvent(SessionEvent):
    """Commit one Harnessed model invocation's final request snapshot.

    The request_id correlates its usage and exactly one assistant message or
    request failure. Provider-internal transport retries retain this identity.
    Retry metadata here describes runtime recovery, not provider SDK counters.
    """

    __hash__ = None

    type: Literal[EventKind.MODEL_REQUESTED] = field(
        default=EventKind.MODEL_REQUESTED, kw_only=True
    )
    request: ModelRequest
    model: ModelInfo = field(kw_only=True)
    request_id: Identifier = field(kw_only=True)
    retry_group_id: Identifier | None = field(default=None, kw_only=True)
    retry_kind: RetryKind | None = field(default=None, kw_only=True)
    attempt: Annotated[int, Field(ge=1)] = field(default=1, kw_only=True)
    max_retries: Annotated[int, Field(ge=0)] | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.retry_kind is not None:
            object.__setattr__(self, "retry_kind", RetryKind(self.retry_kind))


@dataclass(frozen=True)
class ModelRequestFailedEvent(SessionEvent):
    """Close a request without an assistant message; not a turn completion."""

    __hash__ = None

    type: Literal[EventKind.MODEL_REQUEST_FAILED] = field(
        default=EventKind.MODEL_REQUEST_FAILED, kw_only=True
    )
    request_id: Identifier
    outcome: ModelRequestOutcome
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", ModelRequestOutcome(self.outcome))


@dataclass(frozen=True)
class ModelUsageRecordedEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.MODEL_USAGE_RECORDED] = field(
        default=EventKind.MODEL_USAGE_RECORDED, kw_only=True
    )
    usage: ModelUsage
    purpose: UsagePurpose = UsagePurpose.RESPONSE
    request_id: Identifier | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "purpose", UsagePurpose(self.purpose))


@dataclass(frozen=True)
class CacheMetricsEvent(SessionEvent):
    __hash__ = None

    type: Literal[EventKind.CACHE_METRICS] = field(default=EventKind.CACHE_METRICS, kw_only=True)
    usage: ModelUsage
    hit_rate: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "hit_rate", self.usage.cache_hit_rate)


@dataclass(frozen=True, slots=True)
class ModelAttemptStartedEvent(ModelAttemptStarted, TransientEvent):
    """Start an observed provider attempt or a runtime recovery request.

    retry_kind separates provider attempts from context and output retries;
    their counters have different scopes and must not be combined.
    """

    type: Literal[EventKind.MODEL_ATTEMPT_STARTED] = field(
        default=EventKind.MODEL_ATTEMPT_STARTED, kw_only=True
    )
    request_id: str
    retry_group_id: str
    retry_kind: RetryKind = field(default=RetryKind.PROVIDER, kw_only=True)


@dataclass(frozen=True, slots=True)
class ModelAttemptFinishedEvent(ModelAttemptFinished, TransientEvent):
    """Report an attempt outcome within retry_kind, not the turn's outcome.

    Missing provider telemetry permits a finish with unknown counters and no
    preceding start notification. RETRYING closes this attempt, not its group.
    """

    type: Literal[EventKind.MODEL_ATTEMPT_FINISHED] = field(
        default=EventKind.MODEL_ATTEMPT_FINISHED, kw_only=True
    )
    request_id: str
    retry_group_id: str
    retry_kind: RetryKind = field(default=RetryKind.PROVIDER, kw_only=True)


@dataclass(frozen=True, slots=True)
class ModelRetryScheduledEvent(ModelRetryScheduled, TransientEvent):
    """Plan a successor to the reported attempt without advancing its counters.

    The planned attempt may never start, for example on cancellation in backoff.
    """

    type: Literal[EventKind.MODEL_RETRY_SCHEDULED] = field(
        default=EventKind.MODEL_RETRY_SCHEDULED, kw_only=True
    )
    request_id: str
    retry_group_id: str
    retry_kind: RetryKind = field(default=RetryKind.PROVIDER, kw_only=True)
