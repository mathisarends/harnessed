from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, cast

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass as validated_dataclass

from harnessed.core.messages import JSONMapping, freeze
from harnessed.core.model import ToolDefinition


class ToolChain(StrEnum):
    CONTINUE = "continue"
    STOP_AFTER = "stop_after"


@validated_dataclass(frozen=True, config=ConfigDict(strict=True, extra="forbid"))
class ToolAnnotations:
    """Descriptive hints; these never grant local execution or approval permissions."""

    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    idempotent_hint: bool | None = None
    open_world_hint: bool | None = None


@validated_dataclass(frozen=True, config=ConfigDict(strict=True, extra="forbid"))
class ToolIcon:
    """An application-rendered icon reference; Harnessed does not fetch its source."""

    src: str
    mime_type: str | None = None
    sizes: tuple[str, ...] = ()
    theme: Literal["light", "dark"] | None = None


@dataclass(frozen=True, init=False)
class ToolPresentation:
    """Immutable display and integration metadata, separate from model arguments."""

    title: str | None
    tags: frozenset[str]
    icons: tuple[ToolIcon, ...]
    annotations: ToolAnnotations | None
    meta: JSONMapping

    def __init__(
        self,
        *,
        title: str | None = None,
        tags: Iterable[str] = (),
        icons: Iterable[ToolIcon] = (),
        annotations: ToolAnnotations | None = None,
        meta: JSONMapping | None = None,
    ) -> None:
        if title is not None and not isinstance(title, str):
            raise TypeError("Tool title must be a string or None")
        if isinstance(tags, str):
            raise TypeError("Tool tags must be a collection of strings")
        frozen_tags = frozenset(tags)
        if any(not isinstance(tag, str) or not tag for tag in frozen_tags):
            raise TypeError("Tool tags must be nonempty strings")
        frozen_icons = tuple(icons)
        if any(not isinstance(icon, ToolIcon) for icon in frozen_icons):
            raise TypeError("Tool icons must be ToolIcon values")
        if annotations is not None and not isinstance(annotations, ToolAnnotations):
            raise TypeError("Tool annotations must be ToolAnnotations or None")
        if meta is not None and not isinstance(meta, Mapping):
            raise TypeError("Tool meta must be a JSON object or None")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "tags", frozen_tags)
        object.__setattr__(self, "icons", frozen_icons)
        object.__setattr__(self, "annotations", annotations)
        object.__setattr__(self, "meta", freeze({} if meta is None else meta))


@dataclass(frozen=True)
class ToolMetadata:
    """Process-local tool annotations, keyed by exact type.

    The collection is immutable; applications must also keep its values immutable.
    Values are neither sent to the model nor persisted automatically.
    """

    values: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        values = tuple(self.values)
        kinds = [type(value) for value in values]
        if len(set(kinds)) != len(kinds):
            raise ValueError("Tool metadata requires one value per exact type")
        object.__setattr__(self, "values", values)

    def get[T](self, kind: type[T]) -> T | None:
        for value in self.values:
            if type(value) is kind:
                # Exact-type lookup is the heterogeneous collection's typed boundary.
                return cast(T, value)
        return None


@dataclass(frozen=True)
class ToolInfo:
    """Public description of the registration captured for a scheduled call."""

    definition: ToolDefinition
    metadata: ToolMetadata
    readonly: bool
    parallel: bool
    chain: ToolChain
    presentation: ToolPresentation = ToolPresentation()
