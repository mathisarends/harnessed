from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Self
from xml.sax.saxutils import escape, quoteattr

from harnessed.kernel import PluginScope, Scope


class PromptFormat(StrEnum):
    PLAIN = "plain"
    XML = "xml"
    MARKDOWN = "markdown"


class SectionDelivery(StrEnum):
    """How runtime mounts handle declared system sections."""

    ERROR = "error"
    CONTEXT = "context"
    IGNORE = "ignore"


@dataclass(frozen=True, slots=True, eq=False, kw_only=True)
class Instructions:
    """Prompt content supplied as text, a file, or a session-scoped factory."""

    text: str = ""
    title: str | None = None
    description: str = ""
    _file: Path | None = field(default=None, init=False, repr=False)
    _missing_ok: bool = field(default=False, init=False, repr=False)
    _factory: Callable[[PluginScope], Awaitable[str | None]] | None = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not isinstance(self.description, str):
            raise TypeError("System section text and description must be strings")
        if self.title is not None and (
            not isinstance(self.title, str)
            or not self.title.strip()
            or len(self.title.splitlines()) != 1
        ):
            raise ValueError("System section titles must be non-empty single-line strings")

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
        factory: Callable[[PluginScope], Awaitable[str | None]],
        title: str | None = None,
        description: str = "",
    ) -> Self:
        """Evaluate once per session with a PluginScope for owned resources and registrations."""
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

    async def render(self, scope: Scope | PluginScope | None = None) -> str:
        text: str | None = self.text
        if self._factory is not None:
            if scope is None:
                raise ValueError("This system section requires a session context")
            owner = scope.plugin_scope if isinstance(scope, Scope) else scope
            text = await self._factory(owner)
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


type PromptPart = str | Instructions | SystemPrompt


@dataclass(frozen=True, slots=True, init=False)
class SystemPrompt:
    """An ordered document of instructions. Nested sections inherit the outer format."""

    sections: tuple[PromptPart, ...]
    format: PromptFormat

    def __init__(
        self,
        *sections: PromptPart | None,
        format: PromptFormat = PromptFormat.PLAIN,
    ) -> None:
        content = tuple(part for part in sections if part is not None)
        if any(not isinstance(part, str | Instructions | SystemPrompt) for part in content):
            raise TypeError("SystemPrompt sections must be strings, Instructions or SystemPrompt")
        object.__setattr__(self, "sections", content)
        object.__setattr__(self, "format", PromptFormat(format))


@dataclass(frozen=True, slots=True)
class PromptContribution:
    name: str
    instructions: Callable[[], SystemPrompt] | None = None


@dataclass(frozen=True, slots=True)
class PromptEntry:
    section: Instructions
    origin: str


class PromptComposition:
    """Append each selected plugin's instructions after the application document."""

    def __init__(
        self,
        prompt: SystemPrompt | None = None,
        contributions: Iterable[PromptContribution] = (),
    ) -> None:
        document = SystemPrompt() if prompt is None else prompt
        self.document = document
        self.format = document.format
        entries = _flatten(document, "application instructions")
        for contribution in contributions:
            entries.extend(_contributed_entries(contribution))
        self.entries = tuple(entries)

    def __str__(self) -> str:
        lines = [f"System prompt ({self.format.value}, {len(self.entries)} sections)"]
        for index, entry in enumerate(self.entries, 1):
            lines.append(f"  {index}. {entry.origin}: {entry.section.preview!r}")
        return "\n".join(lines)

    async def render(self, scope: Scope | PluginScope | None = None) -> str:
        parts: list[str] = []
        for entry in self.entries:
            text = await entry.section.render(scope)
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


def _flatten(group: SystemPrompt, origin: str) -> list[PromptEntry]:
    entries: list[PromptEntry] = []
    for index, part in enumerate(group.sections, 1):
        match part:
            case SystemPrompt():
                entries.extend(_flatten(part, origin))
            case str():
                entries.append(PromptEntry(Instructions(text=part), origin))
            case Instructions():
                entries.append(PromptEntry(part, f"{origin}: {part.title or index}"))
    return entries


def _contributed_entries(contribution: PromptContribution) -> list[PromptEntry]:
    provider = contribution.instructions
    if provider is None:
        return []
    block = provider()
    if not isinstance(block, SystemPrompt):
        raise TypeError(f"{contribution.name}.instructions() must return a SystemPrompt")
    return _flatten(block, f"{contribution.name}.instructions")
