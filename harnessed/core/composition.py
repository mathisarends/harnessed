from __future__ import annotations

import copy
import warnings
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Self

from harnessed.kernel import Bundle, Context, PluginBase, PluginLike, PluginSpec, ServiceKey
from harnessed.kernel.plugin import as_plugin_spec

from .errors import PluginConflict, PluginDependencyError, PluginDependencyWarning


@dataclass(frozen=True, slots=True)
class PluginDescription:
    """A selected recipe's static declarations and composition history."""

    name: str
    kit_path: tuple[str, ...]
    slot: str | None
    needs: tuple[ServiceKey[Any], ...]
    provides: tuple[ServiceKey[Any], ...]
    replaces: tuple[PluginDescription, ...] = ()

    @property
    def origin(self) -> str:
        """A readable path; an empty kit path denotes an explicit agent input."""
        return (
            " / ".join((*self.kit_path, self.name)) if self.kit_path else f"explicit / {self.name}"
        )


@dataclass(frozen=True, slots=True)
class PluginRemoval:
    """A recipe excluded by without(), including the kit that excluded it."""

    plugin: PluginDescription
    removed_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompositionDescription:
    """Static configured order and provenance, independent of live mount state."""

    plugins: tuple[PluginDescription, ...]
    removed: tuple[PluginRemoval, ...] = ()

    def __str__(self) -> str:
        lines = [f"Agent composition ({len(self.plugins)} plugins)"]

        def history(plugin: PluginDescription, indent: str) -> None:
            for previous in plugin.replaces:
                lines.append(f"{indent}replaces: {previous.origin}")
                history(previous, indent + "  ")

        for plugin in self.plugins:
            lines.append(f"  {plugin.origin}")
            if plugin.slot is not None:
                lines.append(f"    slot: {plugin.slot}")
            for label, keys in (("needs", plugin.needs), ("provides", plugin.provides)):
                if keys:
                    lines.append(f"    {label}: {', '.join(key.__name__ for key in keys)}")
            history(plugin, "    ")
        for removal in self.removed:
            lines.append(
                f"  removed: {removal.plugin.origin} by {' / '.join(removal.removed_by)}.without()"
            )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _Exclusion:
    plugin: PluginLike
    kit_path: tuple[str, ...]


@dataclass(frozen=True, slots=True, init=False)
class Kit:
    """An ordered, immutable bundle of defaults resolved before plugins mount.

    Direct recipes override child-kit contributions by slot. Sibling kits have
    equal precedence regardless of their internal nesting. Recipes without a
    slot are additive. Constructor inputs are captured once, not deep-copied.
    """

    name: str
    plugins: tuple[PluginInput, ...]
    _excluded: tuple[_Exclusion, ...] = field(default=(), repr=False)

    def __init__(self, name: str, plugins: Iterable[PluginInput]) -> None:
        if not isinstance(name, str):
            raise TypeError("Kit name must be a string")
        if not name.strip():
            raise ValueError("Kit name must not be empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "plugins", tuple(plugins))
        object.__setattr__(self, "_excluded", ())

    def without(self, *types: type[object]) -> Self:
        """Return a new kit excluding matching recipe types and their subclasses.

        Search child kits recursively. Every requested type must match at least
        one remaining recipe; no dependency providers are removed implicitly.
        """
        if not all(isinstance(kind, type) for kind in types):
            raise TypeError("without() requires recipe types")
        found: set[type[object]] = set()
        excluded: list[_Exclusion] = []

        def exclude(kit: Kit, path: tuple[str, ...]) -> tuple[PluginInput, ...]:
            plugins: list[PluginInput] = []
            for value in kit.plugins:
                if isinstance(value, Kit):
                    child = copy.copy(value)
                    object.__setattr__(child, "plugins", exclude(value, (*path, value.name)))
                    plugins.append(child)
                    continue
                matches = {kind for kind in types if isinstance(value, kind)}
                if matches:
                    found.update(matches)
                    excluded.append(_Exclusion(value, path))
                else:
                    plugins.append(value)
            return tuple(plugins)

        plugins = exclude(self, ())
        missing = [kind.__name__ for kind in types if kind not in found]
        if missing:
            raise ValueError(
                f"Kit {self.name!r} contains no recipes matching: {', '.join(missing)}"
            )
        result = copy.copy(self)
        object.__setattr__(result, "plugins", plugins)
        object.__setattr__(result, "_excluded", (*self._excluded, *excluded))
        return result


type PluginInput = PluginLike | Kit


@dataclass(frozen=True, slots=True)
class _Entry:
    plugin: PluginLike
    description: PluginDescription


@dataclass(frozen=True, slots=True)
class ResolvedComposition:
    """Resolved recipes and their static description for the agent boundary."""

    entries: tuple[_Entry, ...]
    removed: tuple[PluginRemoval, ...]

    @property
    def plugins(self) -> tuple[PluginLike, ...]:
        return tuple(entry.plugin for entry in self.entries)

    @property
    def description(self) -> CompositionDescription:
        return CompositionDescription(
            tuple(entry.description for entry in self.entries), self.removed
        )


def _describe(spec: PluginSpec, path: tuple[str, ...]) -> PluginDescription:
    return PluginDescription(spec.name, path, spec.slot, spec.needs, spec.provides)


def _merge(candidates: list[tuple[_Entry, bool]]) -> tuple[_Entry, ...]:
    """Resolve one scope; bool marks a direct recipe rather than a kit export."""
    groups: dict[str, list[int]] = {}
    for index, (entry, _) in enumerate(candidates):
        if entry.description.slot is not None:
            groups.setdefault(entry.description.slot, []).append(index)
    selected: dict[int, _Entry] = {}
    for slot, indices in groups.items():
        direct = [index for index in indices if candidates[index][1]]
        eligible = direct or indices
        if len(eligible) > 1:
            origins = ", ".join(candidates[index][0].description.origin for index in eligible)
            raise PluginConflict(f"Multiple plugins in slot {slot!r}: {origins}")
        winner = eligible[0]
        entry = candidates[winner][0]
        previous = tuple(candidates[index][0].description for index in indices if index != winner)
        inherited = [index for index in indices if not candidates[index][1]]
        anchor = inherited[0] if inherited else winner
        selected[anchor] = _Entry(
            entry.plugin,
            replace(entry.description, replaces=(*entry.description.replaces, *previous)),
        )
    return tuple(
        selected[index] if entry.description.slot is not None else entry
        for index, (entry, _) in enumerate(candidates)
        if entry.description.slot is None or index in selected
    )


def _resolve(plugins: Iterable[PluginInput], path: tuple[str, ...]) -> ResolvedComposition:
    candidates: list[tuple[_Entry, bool]] = []
    removed: list[PluginRemoval] = []
    for value in plugins:
        if isinstance(value, Kit):
            child_path = (*path, value.name)
            child = _resolve(value.plugins, child_path)
            candidates.extend((entry, False) for entry in child.entries)
            removed.extend(child.removed)
            removed.extend(
                PluginRemoval(
                    _describe(as_plugin_spec(item.plugin), (*child_path, *item.kit_path)),
                    child_path,
                )
                for item in value._excluded
            )
        else:
            spec = as_plugin_spec(value)
            # Function factories are configured once; kernel install must receive
            # exactly the recipe whose declarations were used for composition.
            recipe = value if isinstance(value, PluginSpec | Bundle | PluginBase) else spec
            candidates.append((_Entry(recipe, _describe(spec, path)), True))
    return ResolvedComposition(_merge(candidates), tuple(removed))


def compose_plugins(
    plugins: Iterable[PluginInput], *, defaults: Iterable[PluginLike] = ()
) -> ResolvedComposition:
    """Resolve user scopes first, then override the agent's fallback recipes."""
    supplied = _resolve(plugins, ())
    fallback = _resolve(defaults, ("harnessed",))
    entries = _merge(
        [(entry, False) for entry in fallback.entries]
        + [(entry, True) for entry in supplied.entries]
    )
    return ResolvedComposition(entries, supplied.removed)


class PluginValidation(StrEnum):
    """How an agent handles plugins still waiting at session startup."""

    ERROR = "error"
    WARN = "warn"


def validate_plugins(ctx: Context, policy: PluginValidation) -> None:
    """Check a settled session scope before starting its runtime."""
    issues = ctx.plugin_diagnostics()
    if not issues:
        return
    if policy == PluginValidation.ERROR:
        raise PluginDependencyError(issues)
    warnings.warn(PluginDependencyWarning(issues), stacklevel=2)
