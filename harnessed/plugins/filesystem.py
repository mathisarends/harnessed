from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import Field

from harnessed.kernel import needs
from harnessed.prompts import Instructions, SystemPrompt
from harnessed.runtime.plugin import Plugin, PluginConfig
from harnessed.tools import tool
from harnessed.tools.workspace import FileStat, FileSystemBackend, SearchResult
from harnessed.tools.workspace import LocalFileSystem as LocalFileSystem
from harnessed.tools.workspace import search_files as search_files


class FilesystemAction(StrEnum):
    """Stable approval keys for the built-in filesystem tools."""

    READ = "read"
    WRITE = "write"
    LIST = "list"
    SEARCH = "search"
    EDIT = "edit"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class PathMatches:
    paths: tuple[str, ...]
    truncated: bool


_FILESYSTEMTOOLS_INSTRUCTIONS = Instructions(
    text=(
        "File paths are relative to the workspace. Read a range before editing. "
        "Use glob and grep to discover relevant files."
    )
)


class Filesystem(Plugin[PluginConfig], slot="filesystem"):
    """Expose workspace tools through a separately owned FileSystemBackend service."""

    files = needs(FileSystemBackend)

    def instructions(self) -> SystemPrompt:
        return SystemPrompt(_FILESYSTEMTOOLS_INSTRUCTIONS)

    @tool(action=FilesystemAction.READ, readonly=True, parallel=True)
    async def read_file(
        self,
        path: str,
        offset: Annotated[int, Field(ge=0, description="Unicode characters to skip")] = 0,
        limit: Annotated[
            int, Field(ge=1, le=16_000, description="Maximum Unicode characters to read")
        ] = 8000,
    ) -> str:
        """Read a UTF-8 character range; use offset/limit for large files."""
        text = await self.files.read(Path(path), offset=offset, limit=limit + 1)
        content = text[:limit]
        if len(text) > limit:
            content += (
                f"\n\n[File read truncated. Continue with offset={offset + limit}, limit={limit}.]"
            )
        return content

    @tool(action=FilesystemAction.WRITE)
    async def write_file(self, path: str, text: str) -> str:
        """Atomically replace a UTF-8 file, creating parent directories if needed."""
        await self.files.write(Path(path), text)
        return f"Wrote {path}"

    @tool(action=FilesystemAction.LIST, readonly=True, parallel=True)
    async def list_dir(self, path: str = ".") -> Sequence[FileStat]:
        """List direct children with file sizes and kinds."""
        return await self.files.list(Path(path))

    @tool(action=FilesystemAction.SEARCH, readonly=True, parallel=True)
    async def glob(
        self,
        pattern: str,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    ) -> PathMatches:
        """Find workspace paths using a glob pattern; results are bounded by limit."""
        paths = await self.files.glob(pattern, limit=limit + 1)
        return PathMatches(tuple(path.as_posix() for path in paths[:limit]), len(paths) > limit)

    @tool(action=FilesystemAction.SEARCH, readonly=True, parallel=True)
    async def grep(
        self,
        query: Annotated[str, Field(min_length=1)],
        pattern: str = "**/*",
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> SearchResult:
        """Find literal text in bounded UTF-8 previews; narrow patterns for large trees."""
        return await self.files.grep(query, pattern=pattern, limit=limit)

    @tool(action=FilesystemAction.EDIT)
    async def edit(
        self,
        path: str,
        old: Annotated[str, Field(min_length=1)],
        new: str,
    ) -> str:
        """Replace exactly one matching text fragment; ambiguous edits fail."""
        await self.files.replace(Path(path), old, new)
        return f"Edited {path}"

    @tool(action=FilesystemAction.DELETE)
    async def delete_file(self, path: str) -> str:
        """Delete one workspace file."""
        await self.files.delete(Path(path))
        return f"Deleted {path}"
