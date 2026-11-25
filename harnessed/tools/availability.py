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
    references: tuple[ToolReference, ...]
    registration: RegistrationState

    def explains(self, selected: str | ToolReference) -> bool:
        """Whether this active recipe excluded the requested name or method."""
        if not self.registration.active:
            return False
        if isinstance(selected, str):
            return any(ref.name == selected for ref in self.references)
        return any(selected.matches_binding(ref.declaration, ref.owner) for ref in self.references)
