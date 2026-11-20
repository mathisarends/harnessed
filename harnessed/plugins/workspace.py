from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Annotated, NamedTuple

from pydantic import Field

from harnessed.kernel import PluginScope, run_sync
from harnessed.runtime.plugin import Plugin, PluginConfig
from harnessed.tools.workspace import FileSystemBackend, LocalFileSystem, LocalShell, ShellBackend


class _EnvironmentVariable(NamedTuple):
    name: str
    value: str


class LocalWorkspaceConfig(PluginConfig):
    root: Path
    env: tuple[_EnvironmentVariable, ...] | None = None
    max_output_bytes: Annotated[int, Field(ge=256)] = 1_048_576


class LocalWorkspace(Plugin[LocalWorkspaceConfig], slot="workspace"):
    """Provide local file and process backends rooted at the same directory per session."""

    def __init__(
        self,
        root: str | Path = ".",
        *,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int = 1_048_576,
    ) -> None:
        variables = (
            None if env is None else tuple(_EnvironmentVariable(k, v) for k, v in env.items())
        )
        super().__init__(
            config=LocalWorkspaceConfig(
                root=Path(root),
                env=variables,
                max_output_bytes=max_output_bytes,
            )
        )

    async def setup(self, scope: PluginScope) -> None:
        files = await run_sync(partial(LocalFileSystem, self.config.root))
        shell = LocalShell(
            root=files.root,
            env=dict(self.config.env) if self.config.env is not None else None,
            max_output_bytes=self.config.max_output_bytes,
        )
        scope.provide(FileSystemBackend, files)
        scope.provide(ShellBackend, shell)


class WorkspaceBackends(Plugin, slot="workspace"):
    """Publish caller-owned backends. The caller manages their lifetime across sessions."""

    def __init__(
        self, *, filesystem: FileSystemBackend | None = None, shell: ShellBackend | None = None
    ) -> None:
        super().__init__()
        if filesystem is None and shell is None:
            raise ValueError("WorkspaceBackends requires a filesystem or shell backend")
        self.filesystem = filesystem
        self.shell = shell

    async def setup(self, scope: PluginScope) -> None:
        if self.filesystem is not None:
            scope.provide(FileSystemBackend, self.filesystem)
        if self.shell is not None:
            scope.provide(ShellBackend, self.shell)
