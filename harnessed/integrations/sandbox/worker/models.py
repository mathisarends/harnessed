from dataclasses import dataclass
from enum import StrEnum


class Operation(StrEnum):
    RUN = "run"
    READ = "read"
    WRITE = "write"
    LIST = "list"
    GLOB = "glob"
    GREP = "grep"
    STAT = "stat"
    EXISTS = "exists"
    DELETE = "delete"
    REPLACE = "replace"
    READ_BYTES = "read_bytes"
    IMPORT = "import"


@dataclass(frozen=True)
class Request:
    operation: Operation
    version: int = 1
    path: str = "."
    text: str = ""
    old: str = ""
    new: str = ""
    offset: int = 0
    limit: int | None = None
    argv: tuple[str, ...] = ()
    timeout: float = 30.0
    max_output_bytes: int = 1_048_576
    max_transfer_bytes: int = 16_777_216


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
