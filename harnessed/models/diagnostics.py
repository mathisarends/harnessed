from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Annotated

from pydantic import Field, NonNegativeFloat, NonNegativeInt, PositiveInt
from pydantic.dataclasses import dataclass

from .values import VALUE_CONFIG


class RetryKind(StrEnum):
    PROVIDER = "provider"
    CONTEXT_RECOVERY = "context_recovery"
    OUTPUT_CORRECTION = "output_correction"


class RetryReason(StrEnum):
    UNKNOWN = "unknown"
    RATE_LIMIT = "rate_limit"
    CONNECTION = "connection"
    SERVER_ERROR = "server_error"
    CONTEXT_LIMIT = "context_limit"
    OUTPUT_VALIDATION = "output_validation"


class ModelAttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYING = "retrying"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"


@dataclass(frozen=True, kw_only=True, config=VALUE_CONFIG)
class _AttemptCounts:
    attempt: PositiveInt | None = None
    retry_count: NonNegativeInt | None = None
    max_retries: NonNegativeInt | None = None

    def __post_init__(self) -> None:
        count = self.attempt - 1 if self.attempt is not None else None
        if self.retry_count is not None and self.retry_count != count:
            raise ValueError("retry_count must equal attempt - 1; unknown attempts use None")
        object.__setattr__(self, "retry_count", count)
        if self.max_retries is not None and count is not None and count > self.max_retries:
            raise ValueError("Observed retries cannot exceed max_retries")


type _StatusCode = Annotated[int, Field(ge=100, le=599)]


@dataclass(frozen=True, kw_only=True, config=VALUE_CONFIG)
class ModelAttemptStarted(_AttemptCounts):
    """An adapter observed an actual provider attempt starting, numbered from one."""


@dataclass(frozen=True, kw_only=True, config=VALUE_CONFIG)
class ModelRetryScheduled(_AttemptCounts):
    """A retry decision after `attempt`; its successor has not started yet."""

    reason: RetryReason = RetryReason.UNKNOWN
    status_code: _StatusCode | None = None
    delay_seconds: NonNegativeFloat | None = None


@dataclass(frozen=True, kw_only=True, config=VALUE_CONFIG)
class ModelAttemptFinished(_AttemptCounts):
    outcome: ModelAttemptOutcome
    reason: RetryReason = RetryReason.UNKNOWN
    status_code: _StatusCode | None = None
    partial: bool = False


type ModelDiagnostic = ModelAttemptStarted | ModelRetryScheduled | ModelAttemptFinished
type ModelDiagnosticSink = Callable[[ModelDiagnostic], Awaitable[None]]

_sink: ContextVar[ModelDiagnosticSink | None] = ContextVar("model_diagnostic_sink", default=None)


@contextmanager
def model_diagnostics(sink: ModelDiagnosticSink) -> Generator[None, None, None]:
    """Bind an awaited adapter callback to this task's request/stream scope.

    This also covers SDK work before a stream's context manager yields. SDK
    instrumentation must report observed facts, not infer hidden attempts.
    """
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


async def report_model_diagnostic(diagnostic: ModelDiagnostic) -> None:
    """Report sanitized retry metadata from an adapter or public SDK callback.

    Outside a diagnostic scope this is a no-op. Await before continuing the
    provider operation; cancellation must propagate through callbacks/backoff.
    """
    if sink := _sink.get():
        await sink(diagnostic)
