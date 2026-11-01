from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum, auto
from pathlib import Path
from typing import Self
from xml.sax.saxutils import escape, quoteattr

from harnessed.core.errors import ConfigError
from harnessed.kernel import Context


class PromptFormat(StrEnum):
    PLAIN = "plain"
    XML = "xml"
    MARKDOWN = "markdown"


class PluginOrder(StrEnum):
    """Order selected plugin instruction blocks by the configured plugin list."""

    AS_LISTED = auto()


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
    _factory: Callable[[Context], Awaitable[str | None]] | None = field(
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


type PromptPart = str | Instructions | SystemPrompt


@dataclass(frozen=True, slots=True, init=False)
class SystemPrompt:
    """An ordered document of actual content; nested documents inherit its format.

    Plugin collection is an explicit whole-document strategy selected with
    from_plugins(order=PluginOrder.AS_LISTED), never an implicit rest slot.
    """

    sections: tuple[PromptPart, ...]
    format: PromptFormat
    plugin_order: PluginOrder | None

    def __init__(
        self,
        *sections: PromptPart | None,
        format: PromptFormat = PromptFormat.PLAIN,
    ) -> None:
        content = tuple(part for part in sections if part is not None)
        if any(not isinstance(part, str | Instructions | SystemPrompt) for part in content):
            raise TypeError("SystemPrompt sections must be strings, Instructions or SystemPrompt")
        if any(
            isinstance(part, SystemPrompt) and part.plugin_order is not None for part in content
        ):
            raise ValueError("Plugin collection belongs to the outermost SystemPrompt")
        object.__setattr__(self, "sections", content)
        object.__setattr__(self, "format", PromptFormat(format))
        object.__setattr__(self, "plugin_order", None)

    @classmethod
    def from_plugins(
        cls,
        *sections: PromptPart | None,
        order: PluginOrder,
        format: PromptFormat = PromptFormat.PLAIN,
    ) -> Self:
        """Append selected plugin blocks in resolved list order after fixed content.

        Nested kits expand at their list position. Removals contribute nothing;
        slot replacements retain the inherited position. Setup order is unrelated.
        The explicit order argument is required even when only AS_LISTED exists.
        """
        document = cls(*sections, format=format)
        object.__setattr__(document, "plugin_order", PluginOrder(order))
        return document


@dataclass(frozen=True, slots=True)
class PromptContribution:
    name: str
    instructions: Callable[[], SystemPrompt] | None = None
    context_description: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class PromptEntry:
    section: Instructions
    origin: str


class PromptComposition:
    """Collect selected recipes once, before taking each session's fixed snapshot."""

    def __init__(
        self,
        prompt: SystemPrompt | None = None,
        contributions: Iterable[PromptContribution] = (),
        *,
        instructions: str | None = None,
    ) -> None:
        document = prompt if prompt is not None else SystemPrompt()
        if not isinstance(document, SystemPrompt):
            raise TypeError("prompt must be a SystemPrompt")
        if instructions is not None and not isinstance(instructions, str):
            raise TypeError("instructions must be a string or None")
        if instructions is not None:
            document = (
                SystemPrompt.from_plugins(
                    instructions,
                    *document.sections,
                    order=document.plugin_order,
                    format=document.format,
                )
                if document.plugin_order is not None
                else SystemPrompt(instructions, *document.sections, format=document.format)
            )
        self.document = document
        self.format = document.format
        contributions = tuple(contributions)
        self.context_entries = tuple(
            description
            for contribution in contributions
            if (description := contribution.context_description) is not None
        )
        providers = tuple(item for item in contributions if item.instructions is not None)
        if providers and document.plugin_order is None:
            raise ConfigError(
                "Plugin instructions require "
                "SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED): "
                + ", ".join(item.name for item in providers)
            )
        entries: list[PromptEntry] = []

        def flatten(group: SystemPrompt, origin: str) -> None:
            for index, part in enumerate(group.sections, 1):
                match part:
                    case SystemPrompt():
                        flatten(part, origin)
                    case str():
                        entries.append(PromptEntry(Instructions(text=part), origin))
                    case Instructions():
                        entries.append(PromptEntry(part, f"{origin}: {part.title or index}"))

        flatten(document, "application instructions")
        for contribution in providers:
            provider = contribution.instructions
            if provider is None:
                continue
            block = provider()
            if not isinstance(block, SystemPrompt):
                raise TypeError(f"{contribution.name}.instructions() must return a SystemPrompt")
            if block.plugin_order is not None:
                raise ValueError(
                    f"{contribution.name}.instructions() must return content, not plugin collection"
                )
            flatten(block, f"{contribution.name}.instructions")
        self.entries = tuple(entries)

    def __str__(self) -> str:
        lines = [f"System prompt ({self.format.value}, {len(self.entries)} sections)"]
        for index, entry in enumerate(self.entries, 1):
            lines.append(f"  {index}. {entry.origin}: {entry.section.preview!r}")
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
