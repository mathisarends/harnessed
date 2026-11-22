from enum import StrEnum
from typing import Annotated

from pydantic import Field
from pydantic.dataclasses import dataclass

from harnessed.models.values import VALUE_CONFIG


class ChildActivity(StrEnum):
    QUEUED = "queued"
    MODEL = "model"
    TOOL = "tool"
    WAITING_APPROVAL = "waiting_approval"
    IDLE = "idle"
    FINISHED = "finished"


class ChildReportState(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, config=VALUE_CONFIG)
class ChildReport:
    """A correlated, explicit child answer, separate from its task result."""

    request_id: Annotated[str, Field(min_length=1)]
    state: ChildReportState
    text: Annotated[str, Field(max_length=8192)] = ""


@dataclass(frozen=True, config=VALUE_CONFIG)
class ChildRuntimeState:
    """Bounded observation of a child; contains neither history nor reasoning."""

    activity: ChildActivity = ChildActivity.QUEUED
    steps_completed: Annotated[int, Field(ge=0)] = 0
    step_limit: Annotated[int, Field(ge=0)] = 0
    active_tools: tuple[str, ...] = ()
    last_progress: Annotated[str, Field(max_length=4096)] | None = None
    progress_at: float | None = None
    report: ChildReport | None = None
