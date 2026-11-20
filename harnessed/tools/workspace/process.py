from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


class ShellBackend(Protocol):
    """Run argv with a deadline including queueing, launch and execution.

    Expiry raises TimeoutError; cancellation propagates CancelledError after
    owned cleanup. Failed commands must never be replayed automatically.
    Configure environment and capture limits on the backend itself.
    """

    async def run(self, argv: list[str], *, cwd: Path, timeout: float) -> ProcResult:
        """Run argv within timeout seconds; drain owned cleanup before returning or raising."""
        ...


class ProcessState(StrEnum):
    RUNNING = "running"
    EXITED = "exited"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    """A bounded, repeatable snapshot; reading never consumes process output."""

    state: ProcessState
    returncode: int | None
    stdout: str
    stderr: str
    error: str | None = None


class ShellProcess(Protocol):
    """One owned command. Terminal snapshots are available after cleanup finishes.

    Cancelling wait() must leave the command running. terminate() is idempotent,
    kills descendants and drains cleanup before returning. write() sends UTF-8
    input, optionally followed by EOF. Output remains bounded while running.
    """

    def snapshot(self) -> ProcessSnapshot:
        """Return bounded current output and state without consuming output."""
        ...

    async def wait(self) -> ProcessSnapshot:
        """Wait for a terminal snapshot after cleanup; cancelling this wait leaves it running."""
        ...

    async def write(self, text: str, *, eof: bool = False) -> None:
        """Send UTF-8 input and optionally EOF to the running process."""
        ...

    async def terminate(self) -> ProcessSnapshot:
        """Idempotently kill the process tree and return its terminal snapshot after cleanup."""
        ...


@runtime_checkable
class BackgroundShellBackend(ShellBackend, Protocol):
    """Optional interactive capability, independent of the foreground contract.

    start() returns promptly without waiting for exit. Cancellation during start
    must clean up any launched process. A returned handle owns its deadline and
    process tree until termination, including after its parent tool call ends.
    """

    async def start(self, argv: list[str], *, cwd: Path, timeout: float) -> ShellProcess:
        """Launch within timeout seconds and return an owned handle without waiting for exit."""
        ...
