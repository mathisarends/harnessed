from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harnessed.kernel import RegistrationState, ServiceKey
from harnessed.models import ToolDefinition

from .references import ToolReference


class ToolAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING_SERVICES = "missing_services"
    DISABLED = "disabled"
    EXCLUDED = "excluded"
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


@dataclass(frozen=True)
class RecipeExclusions:
    references: frozenset[ToolReference]
    registration: RegistrationState

    def explains(self, name: str) -> bool:
        """Whether this active recipe excluded a declaration with the model-facing name."""
        return self.registration.active and any(ref.name == name for ref in self.references)
