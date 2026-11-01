from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Protocol, Self, overload
from xml.sax.saxutils import escape, quoteattr

from harnessed.core.errors import ConfigError
from harnessed.kernel import Context


class PromptFormat(StrEnum):
    PLAIN = "plain"
    XML = "xml"
    MARKDOWN = "markdown"


class Unplaced(StrEnum):
    """Append remaining plugin sections, or require complete placement.

    ERROR checks coverage, not prompt stability: plugin groups and the rest slot
    also place future declarations. Use individual references to detect additions.
    """

    APPEND = "append"
    ERROR = "error"


class SectionDelivery(StrEnum):
    """How runtime mounts handle declared system sections."""

    ERROR = "error"
    CONTEXT = "context"
    IGNORE = "ignore"


class PromptOwner(Protocol):
    name: str


@dataclass(frozen=True, slots=True, eq=False, kw_only=True)
class Instructions:
    """System instructions supplied as text, a file, or a session-scoped factory.

    Access on a plugin recipe binds its identity and diagnostic name to that
    recipe without evaluating content.
    """

    text: str = ""
    title: str | None = None
    description: str = ""
    _file: Path | None = field(default=None, init=False, repr=False)
    _missing_ok: bool = field(default=False, init=False, repr=False)
    _factory: Callable[[Context], Awaitable[str | None]] | None = field(
        default=None, init=False, repr=False
    )
    _attribute: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not isinstance(self.description, str):
            raise TypeError("System section text and description must be strings")
        if self.title is not None and (
            not isinstance(self.title, str)
            or not self.title.strip()
            or len(self.title.splitlines()) != 1
        ):
            raise ValueError("System section titles must be non-empty single-line strings")

    def __set_name__(self, owner: type, name: str) -> None:
        object.__setattr__(self, "_attribute", name)

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> Self: ...

    @overload
    def __get__(self, instance: PromptOwner, owner: type | None = None) -> InstructionRef: ...

    def __get__(
        self, instance: PromptOwner | None, owner: type | None = None
    ) -> Self | InstructionRef:
        return self if instance is None else self.bind(instance, self._attribute)

    def bind(self, owner: PromptOwner, attribute: str) -> InstructionRef:
        return InstructionRef(owner=owner, declaration=self, attribute=attribute, section=self)

    @classmethod
    def from_file(
        cls, *, path: str | Path, title: str | None = None, missing_ok: bool = False
    ) -> Self:
        section = cls(title=title)
        object.__setattr__(section, "_file", Path(path))
        object.__setattr__(section, "_missing_ok", missing_ok)
        return section

    @classmethod
    def from_factory(
        cls,
        *,
        factory: Callable[[Context], Awaitable[str | None]],
        title: str | None = None,
        description: str = "",
    ) -> Self:
        """Evaluate once per session, with session-owned registrations and cleanup."""
        if not callable(factory):
            raise TypeError("System section factories must be callable")
        section = cls(title=title, description=description)
        object.__setattr__(section, "_factory", factory)
        return section

    @property
    def literal_text(self) -> str | None:
        return self.text if self._file is None and self._factory is None else None

    @property
    def preview(self) -> str:
        return self.description or (str(self._file) if self._file else self.text[:80])

    async def render(self, ctx: Context | None = None) -> str:
        text: str | None = self.text
        if self._factory is not None:
            if ctx is None:
                raise ValueError("This system section requires a session context")
            text = await self._factory(ctx)
        elif self._file is not None:
            try:
                text = await asyncio.to_thread(self._file.read_text, encoding="utf-8")
            except FileNotFoundError:
                if not self._missing_ok:
                    raise
                text = ""
        if text is not None and not isinstance(text, str):
            raise TypeError("System section factories must return a string or None")
        return text or ""


@dataclass(frozen=True, slots=True, eq=False, kw_only=True)
class InstructionRef:
    """A section reference bound to one configured plugin recipe."""

    owner: PromptOwner
    declaration: object
    attribute: str
    section: Instructions
    provider: Callable[[], str | None] | None = field(default=None, repr=False)

    @property
    def identity(self) -> tuple[int, int]:
        return id(self.owner), id(self.declaration)

    @property
    def origin(self) -> str:
        return f"{self.owner.name}.{self.attribute}"

    def resolve(self) -> Instructions:
        if self.provider is None:
            return self.section
        text = self.provider()
        if text is not None and not isinstance(text, str):
            raise TypeError(f"System section {self.origin} must return a string or None")
        return replace(self.section, text=text or "")


class InstructionProvider[T: PromptOwner]:
    def __init__(self, callback: Callable[[T], str | None], *, title: str | None) -> None:
        self.callback = callback
        self.section = Instructions(title=title)
        self.attribute = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.attribute = name

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> Self: ...

    @overload
    def __get__(self, instance: T, owner: type | None = None) -> InstructionRef: ...

    def __get__(self, instance: T | None, owner: type | None = None) -> Self | InstructionRef:
        if instance is None:
            return self
        return InstructionRef(
            owner=instance,
            declaration=self,
            attribute=self.attribute,
            section=self.section,
            provider=partial(self.callback, instance),
        )


def instructions[T: PromptOwner](  # smelt: ignore[SMT316] -- Lazy SDK export.
    *, title: str | None = None
) -> Callable[[Callable[[T], str | None]], InstructionProvider[T]]:
    """Declare pure, synchronous system instructions derived from recipe configuration."""

    def decorate(callback: Callable[[T], str | None]) -> InstructionProvider[T]:
        if inspect.iscoroutinefunction(callback) or inspect.isasyncgenfunction(callback):
            raise TypeError("@instructions requires a synchronous configuration provider")
        return InstructionProvider(callback, title=title)

    return decorate


@dataclass(frozen=True, slots=True)
class PluginInstructions:
    """Place one plugin's sections, or all remaining sections when plugin is None.

    A complete prompt may contain at most one remaining-sections slot, including
    nested documents. Explicit placements and exclusions are resolved first.
    """

    plugin: PromptOwner | None = None


@dataclass(frozen=True, slots=True)
class InstructionFragment:
    """A named kit-owned group, resolved against the final plugin selection."""

    owner: object
    name: str


type PromptPart = (
    str | Instructions | InstructionRef | PluginInstructions | InstructionFragment | SystemPrompt
)


@dataclass(frozen=True, slots=True, init=False)
class SystemPrompt:
    """An ordered document requiring explicit placement or exclusion of instructions.

    Nested documents inherit the outermost format and unplaced policy.
    """

    sections: tuple[PromptPart, ...]
    format: PromptFormat
    exclude: tuple[InstructionRef | Instructions | InstructionFragment, ...]
    unplaced: Unplaced

    def __init__(
        self,
        *,
        sections: Iterable[PromptPart] = (),
        format: PromptFormat = PromptFormat.PLAIN,
        exclude: Iterable[InstructionRef | Instructions | InstructionFragment] = (),
        unplaced: Unplaced = Unplaced.ERROR,
    ) -> None:
        object.__setattr__(self, "sections", tuple(sections))
        object.__setattr__(self, "format", PromptFormat(format))
        object.__setattr__(self, "exclude", tuple(exclude))
        object.__setattr__(self, "unplaced", Unplaced(unplaced))


@dataclass(frozen=True, slots=True)
class PromptContribution:
    name: str
    sections: tuple[InstructionRef, ...] = ()
    context_description: tuple[str, str] | None = None
    owner: PromptOwner | None = None


@dataclass(frozen=True, slots=True)
class PromptEntry:
    section: Instructions
    origin: str


class PromptComposition:
    """Validate placement before evaluating any configuration-derived sections."""

    def __init__(
        self,
        prompt: str | SystemPrompt | None = None,
        contributions: Iterable[PromptContribution] = (),
        *,
        fragments: Mapping[tuple[int, str], tuple[PromptOwner, ...]] | None = None,
    ) -> None:
        contributions = tuple(contributions)
        if isinstance(prompt, str):
            prompt = SystemPrompt(sections=(prompt,))
        if prompt is None:
            prompt = SystemPrompt()
        if not isinstance(prompt, SystemPrompt):
            raise TypeError("system_prompt must be a string or SystemPrompt")
        self.format = prompt.format
        self.context_entries = tuple(
            description
            for contribution in contributions
            if (description := contribution.context_description) is not None
        )
        available = {
            section.identity: section
            for contribution in contributions
            for section in contribution.sections
        }
        by_declaration: dict[int, list[InstructionRef]] = {}
        for section in available.values():
            by_declaration.setdefault(id(section.declaration), []).append(section)
        selected: list[Instructions | InstructionRef | PluginInstructions] = []
        excluded: list[Instructions | InstructionRef] = []

        def fragment(value: InstructionFragment) -> tuple[PromptOwner, ...]:
            key = (id(value.owner), value.name)
            if fragments is None or key not in fragments:
                raise ConfigError(
                    f"Instruction fragment {value.name!r} belongs to an uninstalled kit"
                )
            return fragments[key]

        def flatten(group: SystemPrompt) -> None:
            for value in group.exclude:
                if isinstance(value, InstructionFragment):
                    for owner in fragment(value):
                        excluded.extend(
                            section for section in available.values() if section.owner is owner
                        )
                else:
                    excluded.append(value)
            for part in group.sections:
                if isinstance(part, SystemPrompt):
                    flatten(part)
                elif isinstance(part, InstructionFragment):
                    selected.extend(PluginInstructions(owner) for owner in fragment(part))
                elif isinstance(part, str):
                    selected.append(Instructions(text=part))
                elif isinstance(part, Instructions | InstructionRef | PluginInstructions):
                    selected.append(part)
                else:
                    raise TypeError(
                        "SystemPrompt sections must be strings, section references or groups"
                    )

        flatten(prompt)
        seen: set[tuple[int, int]] = set()
        literal_seen: set[int] = set()

        def reference(
            value: Instructions | InstructionRef,
        ) -> Instructions | InstructionRef:
            if isinstance(value, InstructionRef):
                if value.identity not in available:
                    raise ConfigError(
                        f"System section {value.origin} belongs to an uninstalled plugin"
                    )
                return available[value.identity]
            if not isinstance(value, Instructions):
                raise TypeError("Exclusions must be system section references")
            matches = by_declaration.get(id(value), [])
            if len(matches) > 1:
                raise ConfigError("Ambiguous section; reference it through a configured plugin")
            return matches[0] if matches else value

        def claim(value: Instructions | InstructionRef, *, exclusion: bool) -> None:
            if isinstance(value, InstructionRef):
                if value.identity in seen:
                    raise ConfigError(f"System section {value.origin} was placed or excluded twice")
                seen.add(value.identity)
            else:
                if exclusion:
                    raise ConfigError("Excluded section does not belong to an installed plugin")
                if id(value) in literal_seen:
                    raise ConfigError("The same system section was placed twice")
                literal_seen.add(id(value))

        excluded = [reference(value) for value in excluded]
        for value in excluded:
            claim(value, exclusion=True)
        excluded_ids = set(seen)
        placed: list[Instructions | InstructionRef | PluginInstructions] = []
        rest: PluginInstructions | None = None
        for value in selected:
            if isinstance(value, PluginInstructions):
                if value.plugin is None:
                    if rest is not None:
                        raise ConfigError(
                            "Only one PluginInstructions() rest slot is allowed per prompt"
                        )
                    rest = value
                    placed.append(value)
                    continue
                contribution = next(
                    (item for item in contributions if item.owner is value.plugin), None
                )
                if contribution is None:
                    raise ConfigError("PluginInstructions refers to an uninstalled plugin")
                for section in contribution.sections:
                    if section.identity not in excluded_ids:
                        claim(section, exclusion=False)
                        placed.append(section)
            else:
                section = reference(value)
                claim(section, exclusion=False)
                placed.append(section)
        remaining = [section for identity, section in available.items() if identity not in seen]
        resolved: list[Instructions | InstructionRef] = []
        for value in placed:
            if isinstance(value, PluginInstructions):
                resolved.extend(remaining)
            else:
                resolved.append(value)
        if rest is None:
            if remaining and prompt.unplaced is Unplaced.ERROR:
                raise ConfigError(
                    "Unplaced system sections: "
                    + ", ".join(section.origin for section in remaining)
                    + ". Place each explicitly or exclude it."
                )
            resolved.extend(remaining)
        self.excluded = tuple(
            value.origin for value in excluded if isinstance(value, InstructionRef)
        )
        self.entries = tuple(
            PromptEntry(value.resolve(), value.origin)
            if isinstance(value, InstructionRef)
            else PromptEntry(value, value.title or "application section")
            for value in resolved
        )

    def __str__(self) -> str:
        lines = [f"System prompt ({self.format.value}, {len(self.entries)} sections)"]
        for index, entry in enumerate(self.entries, 1):
            lines.append(f"  {index}. {entry.origin}: {entry.section.preview!r}")
        lines.extend(f"  excluded: {origin}" for origin in self.excluded)
        if self.context_entries:
            lines.append("Context (dynamic, outside the system prompt)")
            lines.extend(f"  - {key}: {preview}" for key, preview in self.context_entries)
        return "\n".join(lines)

    async def render(self, ctx: Context | None = None) -> str:
        parts: list[str] = []
        for entry in self.entries:
            text = await entry.section.render(ctx)
            if not text:
                continue
            title = entry.section.title
            match self.format:
                case PromptFormat.PLAIN:
                    parts.append(text)
                case PromptFormat.MARKDOWN:
                    parts.append(f"## {title}\n\n{text}" if title else text)
                case PromptFormat.XML:
                    attribute = f" title={quoteattr(title)}" if title else ""
                    parts.append(f"<section{attribute}>\n{escape(text)}\n</section>")
        return "\n\n".join(parts)
