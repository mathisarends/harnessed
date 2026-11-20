from __future__ import annotations

import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from harnessed.errors import PluginConflict, PluginDependencyError, PluginDependencyWarning
from harnessed.kernel import (
    Scope,
    ServiceKey,
    as_plugin_spec,
)
from harnessed.models import Model

from .plugin import Plugin, Service

if TYPE_CHECKING:
    from .agent_loop import AgentLoop


@dataclass(frozen=True, slots=True)
class PluginDescription:
    """A configured recipe's static declarations."""

    name: str
    slot: str | None
    needs: tuple[ServiceKey[Any], ...]
    provides: tuple[ServiceKey[Any], ...]
    observes: tuple[ServiceKey[Any], ...] = ()


@dataclass(frozen=True, slots=True)
class CompositionDescription:
    """The configured plugins in mount order, independent of live mount state."""

    model: str | None
    loop: str
    plugins: tuple[PluginDescription, ...]

    def __str__(self) -> str:
        lines = [
            f"Model: {self.model}",
            f"Loop: {self.loop}",
            f"Agent composition ({len(self.plugins)} plugins)",
        ]
        for plugin in self.plugins:
            lines.append(f"  {plugin.name}")
            if plugin.slot is not None:
                lines.append(f"    slot: {plugin.slot}")
            for label, keys in (
                ("needs", plugin.needs),
                ("observes", plugin.observes),
                ("provides", plugin.provides),
            ):
                if keys:
                    lines.append(f"    {label}: {', '.join(key.__name__ for key in keys)}")
        return "\n".join(lines)


def compose_plugins(
    plugins: Iterable[Plugin], *, model: Model | None, loop: AgentLoop
) -> CompositionDescription:
    """Describe the supplied recipes in order; a slot may be claimed only once.

    Nothing is replaced or removed implicitly: two recipes claiming the same
    slot raise ``PluginConflict`` naming both.
    """
    descriptions: list[PluginDescription] = []
    claimed: dict[str, str] = {}
    for value in plugins:
        if not isinstance(value, Plugin):
            raise TypeError("Expected a Plugin instance")
        if isinstance(value, Service) and value.key is Model:
            raise PluginConflict("Configure the model with Agent(model=...)")
        spec = as_plugin_spec(value)
        if spec.slot is not None:
            previous = claimed.get(spec.slot)
            if previous is not None:
                raise PluginConflict(
                    f"Multiple plugins in slot {spec.slot!r}: {previous}, {spec.name}"
                )
            claimed[spec.slot] = spec.name
        descriptions.append(
            PluginDescription(spec.name, spec.slot, spec.needs, spec.provides, spec.observes)
        )
    return CompositionDescription(
        model.info.name if model is not None else None, repr(loop), tuple(descriptions)
    )


class PluginValidation(StrEnum):
    """How an agent handles plugins still waiting at session startup."""

    ERROR = "error"
    WARN = "warn"


def validate_plugins(scope: Scope, policy: PluginValidation) -> None:
    """Check a settled session scope before starting its runtime."""
    issues = scope.plugin_diagnostics()
    if not issues:
        return
    if policy == PluginValidation.ERROR:
        raise PluginDependencyError(issues)
    warnings.warn(PluginDependencyWarning(issues), stacklevel=2)
