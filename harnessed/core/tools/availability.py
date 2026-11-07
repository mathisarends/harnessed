from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harnessed.core.model import ToolDefinition
from harnessed.kernel import ServiceKey


class ToolAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING_SERVICES = "missing_services"
    DISABLED = "disabled"
    NOT_REGISTERED = "not_registered"


@dataclass(frozen=True)
class ToolExplanation:
    """An availability snapshot of the current registration, without authorizing a call.

    NOT_REGISTERED also covers tools whose plugin has not activated. No dormant
    plugin declarations are inferred from the registry.
    """

    name: str
    availability: ToolAvailability
    definition: ToolDefinition | None = None
    missing_services: tuple[ServiceKey[Any], ...] = ()
