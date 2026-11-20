from __future__ import annotations

import asyncio
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, model_validator
from pydantic.fields import FieldInfo

from harnessed.kernel import PluginScope, needs
from harnessed.models import ToolResult, ToolStatus
from harnessed.models.messages import result_content, to_dict
from harnessed.prompts import Instructions, SystemPrompt
from harnessed.runtime.hooks import SessionClosing
from harnessed.runtime.inbox import SessionInbox
from harnessed.runtime.plugin import Plugin, PluginConfig
from harnessed.tools import ToolError, ToolRegistry, tool
from harnessed.tools.workspace import BackgroundShellBackend as BackgroundShellBackend
from harnessed.tools.workspace import LocalShell as LocalShell
from harnessed.tools.workspace import ProcessSnapshot as ProcessSnapshot
from harnessed.tools.workspace import ProcessState as ProcessState
from harnessed.tools.workspace import ProcResult as ProcResult
from harnessed.tools.workspace import ShellBackend as ShellBackend
from harnessed.tools.workspace import ShellProcess as ShellProcess

from .background import BackgroundCommands, command_result
from .background import CommandInfo as CommandInfo
from .background import CommandResult as CommandResult


class ShellAction(StrEnum):
    """Stable approval keys for the built-in shell tools."""

    EXECUTE = "execute"
    START = "start"
    READ_OUTPUT = "read_output"
    WRITE_INPUT = "write_input"
    TERMINATE = "terminate"
    LIST = "list"


def proc_result(value: ProcResult) -> ToolResult:
    """Preserve process failure semantics without a shell dependency in core."""
    return ToolResult(
        result_content(to_dict(value)),
        ToolStatus.OK if value.returncode == 0 else ToolStatus.ERROR,
        exit_code=value.returncode,
    )


_SHELL_INSTRUCTIONS = Instructions(
    text=("Run executables with argv. Filter large output and check exit codes before continuing.")
)


class ShellConfig(PluginConfig):
    cwd: Path = Path()
    timeout: Annotated[float, Field(gt=0, le=600)] = 30.0
    max_timeout: Annotated[float, Field(gt=0, le=600)] = 600.0

    @model_validator(mode="after")
    def validate_timeout(self) -> Self:
        if self.timeout > self.max_timeout:
            raise ValueError("timeout must not exceed max_timeout")
        return self


class Shell(Plugin[ShellConfig], slot="shell"):
    """Expose commands through a separately owned, replaceable ShellBackend."""

    backend = needs(ShellBackend)

    def __init__(
        self,
        cwd: str | Path = ".",
        *,
        timeout: float = 30.0,
        max_timeout: float = 600.0,
    ) -> None:
        super().__init__(
            config=ShellConfig(cwd=Path(cwd), timeout=timeout, max_timeout=max_timeout)
        )

    def instructions(self) -> SystemPrompt:
        return SystemPrompt(_SHELL_INSTRUCTIONS)

    async def setup(self, scope: PluginScope) -> None:
        self.cwd = self.config.cwd
        scope[ToolRegistry].result_converter(ProcResult, proc_result, owner=scope)

    def _command_fields(self) -> Mapping[str, FieldInfo]:
        return {"timeout": Field(le=self.config.max_timeout)}

    @tool(action=ShellAction.EXECUTE, timeout_seconds=600, parameter_fields=_command_fields)
    async def run_command(
        self,
        argv: Annotated[list[str], Field(min_length=1)],
        timeout: Annotated[float, Field(gt=0, le=600)] | None = None,
    ) -> ProcResult:
        """Run argv without shell expansion. Output and execution time are bounded."""
        limit = timeout if timeout is not None else self.config.timeout
        if limit > self.config.max_timeout:
            raise ToolError(f"timeout must not exceed {self.config.max_timeout:g} seconds")
        return await self.backend.run(argv, cwd=self.cwd, timeout=limit)


class BackgroundShellConfig(ShellConfig):
    timeout: Annotated[float, Field(gt=0, le=600)] = 600.0
    max_background_commands: Annotated[int, Field(ge=1)] = 32


_BACKGROUNDSHELL_INSTRUCTIONS = Instructions(
    text=(
        "Use start_command for long shell work, then continue other tasks. "
        "Completion arrives through the session inbox, including while idle. "
        "read_command inspects live output; write_command sends stdin or EOF; "
        "terminate_command kills a dead end before retrying. list_commands finds retained IDs."
    )
)


class BackgroundShell(Plugin[BackgroundShellConfig]):
    """Opt in to session-owned background commands on an interactive ShellBackend."""

    backend = needs(ShellBackend)
    inbox = needs(SessionInbox)

    def __init__(
        self,
        cwd: str | Path = ".",
        *,
        timeout: float = 600.0,
        max_timeout: float = 600.0,
        max_background_commands: int = 32,
    ) -> None:
        super().__init__(
            config=BackgroundShellConfig(
                cwd=Path(cwd),
                timeout=timeout,
                max_timeout=max_timeout,
                max_background_commands=max_background_commands,
            )
        )

    def instructions(self) -> SystemPrompt:
        return SystemPrompt(_BACKGROUNDSHELL_INSTRUCTIONS)

    async def setup(self, scope: PluginScope) -> None:
        self.cwd = self.config.cwd
        self._commands = BackgroundCommands(self.config.max_background_commands)
        scope.effect(self._commands.close)
        scope.on(SessionClosing, self._close_commands)
        scope[ToolRegistry].result_converter(CommandResult, command_result, owner=scope)

    async def _close_commands(self, _event: SessionClosing) -> None:
        await self._commands.close()

    def _command_fields(self) -> Mapping[str, FieldInfo]:
        return {"timeout": Field(le=self.config.max_timeout)}

    @tool(action=ShellAction.START, timeout_seconds=600, parameter_fields=_command_fields)
    async def start_command(
        self,
        argv: Annotated[list[str], Field(min_length=1)],
        timeout: Annotated[float, Field(gt=0, le=600)] | None = None,
    ) -> CommandResult:
        """Start argv without shell expansion and return a command_id without waiting for exit.

        Completion notifies the agent. Use read_command for live output, write_command
        for stdin and terminate_command to stop a dead end. The timeout bounds the
        command's entire lifetime, independently of this tool call.
        """
        limit = timeout if timeout is not None else self.config.timeout
        if limit > self.config.max_timeout:
            raise ToolError(f"timeout must not exceed {self.config.max_timeout:g} seconds")
        shell = self.backend
        if not isinstance(shell, BackgroundShellBackend):
            raise ToolError("This ShellBackend does not support background commands")
        return await self._commands.start(
            shell, argv, cwd=self.cwd, timeout=limit, inbox=self.inbox
        )

    @tool(action=ShellAction.READ_OUTPUT, readonly=True, parallel=True, timeout_seconds=35)
    async def read_command(
        self, command_id: str, wait: Annotated[float, Field(ge=0, le=30)] = 0.0
    ) -> CommandResult:
        """Read bounded stdout/stderr and state, optionally waiting up to 30 seconds for exit.

        Repeated reads return snapshots without consuming output. A cancelled read
        or an expired wait leaves the background command running.
        """
        command = self._commands.get(command_id)
        if wait:
            try:
                async with asyncio.timeout(wait):
                    await command.process.wait()
            except TimeoutError:
                pass
        return command.result()

    @tool(action=ShellAction.WRITE_INPUT)
    async def write_command(
        self,
        command_id: str,
        text: Annotated[str, Field(max_length=65536)] = "",
        *,
        eof: bool = False,
    ) -> CommandResult:
        """Write UTF-8 text to background stdin; eof=True closes stdin after this input.

        This is a pipe, not a terminal: use terminate_command to kill a command.
        """
        command = self._commands.get(command_id)
        await command.process.write(text, eof=eof)
        return command.result()

    @tool(action=ShellAction.TERMINATE, timeout_seconds=600)
    async def terminate_command(self, command_id: str) -> CommandResult:
        """Kill a background command and its descendants, draining cleanup before returning.

        Safe to repeat on a completed command. Use a fresh start_command to retry.
        """
        command = self._commands.get(command_id)
        await command.process.terminate()
        return command.result()

    @tool(action=ShellAction.LIST, readonly=True, parallel=True)
    def list_commands(self) -> tuple[CommandInfo, ...]:
        """List this mount's retained command IDs, argv, states and exit codes."""
        return self._commands.summaries()


__all__ = [
    "BackgroundShell",
    "BackgroundShellBackend",
    "BackgroundShellConfig",
    "CommandInfo",
    "CommandResult",
    "LocalShell",
    "ProcResult",
    "ProcessSnapshot",
    "ProcessState",
    "Shell",
    "ShellAction",
    "ShellBackend",
    "ShellConfig",
    "ShellProcess",
    "proc_result",
]
