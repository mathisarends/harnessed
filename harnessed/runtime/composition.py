import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harnessed.errors import PluginConflict, PluginDependencyError, PluginDependencyWarning
from harnessed.kernel import (
    Bundle,
    PluginBase,
    PluginLike,
    PluginSpec,
    Scope,
    ServiceKey,
    as_plugin_spec,
)


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

    plugins: tuple[PluginDescription, ...]

    def __str__(self) -> str:
        lines = [f"Agent composition ({len(self.plugins)} plugins)"]
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


@dataclass(frozen=True, slots=True)
class ResolvedComposition:
    """Configured recipes and their static description for the agent boundary."""

    plugins: tuple[PluginLike, ...]
    description: CompositionDescription


def compose_plugins(plugins: Iterable[PluginLike | None]) -> ResolvedComposition:
    """Describe the supplied recipes in order; a slot may be claimed only once.

    Nothing is replaced or removed implicitly: two recipes claiming the same
    slot raise ``PluginConflict`` naming both.
    """
    recipes: list[PluginLike] = []
    descriptions: list[PluginDescription] = []
    claimed: dict[str, str] = {}
    for value in plugins:
        if value is None:
            continue
        spec = as_plugin_spec(value)
        if spec.slot is not None:
            previous = claimed.get(spec.slot)
            if previous is not None:
                raise PluginConflict(
                    f"Multiple plugins in slot {spec.slot!r}: {previous}, {spec.name}"
                )
            claimed[spec.slot] = spec.name
        # Function factories are configured once; kernel install must receive
        # exactly the recipe whose declarations were described here.
        recipes.append(value if isinstance(value, PluginSpec | Bundle | PluginBase) else spec)
        descriptions.append(
            PluginDescription(spec.name, spec.slot, spec.needs, spec.provides, spec.observes)
        )
    return ResolvedComposition(tuple(recipes), CompositionDescription(tuple(descriptions)))


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
