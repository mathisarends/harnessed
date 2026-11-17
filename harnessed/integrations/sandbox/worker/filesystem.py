import base64
import fnmatch
import io
import json
import os
import stat
import sys
import tarfile
import uuid
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import PurePosixPath

from .models import Operation, Request


def parts(path: str) -> tuple[str, ...]:
    value = PurePosixPath(path)
    if value.is_absolute() or ".." in value.parts or any(c in path for c in ("\\", ":", "\0")):
        raise ValueError("File paths must stay relative to the workspace")
    return value.parts


@contextmanager
def parent(path: str, *, create: bool = False) -> Generator[tuple[int, str]]:
    """Pin directories with openat; never resolve then reopen an attacker-controlled path."""
    if sys.platform == "win32":
        raise RuntimeError("The sandbox worker must execute inside a POSIX sandbox")
    components = parts(path)
    fd = os.open("/workspace", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in components[:-1]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(name, dir_fd=fd)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, components[-1] if components else "."
    finally:
        os.close(fd)


@contextmanager
def opened(path: str, *, directory: bool = False) -> Generator[int]:
    if sys.platform == "win32":
        raise RuntimeError("The sandbox worker must execute inside a POSIX sandbox")
    with parent(path) as (fd, name):
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        child = os.open(name, flags | (os.O_DIRECTORY if directory else 0), dir_fd=fd)
        try:
            info = os.fstat(child)
            if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
                raise ValueError("Expected a directory" if directory else "Expected a regular file")
            yield child
        finally:
            os.close(child)


def metadata(path: str) -> dict[str, str | int | float | bool]:
    with parent(path) as (fd, name):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError("Links and special files are not supported")
    return {
        "path": PurePosixPath(path).as_posix(),
        "size": info.st_size,
        "is_dir": stat.S_ISDIR(info.st_mode),
        "modified": info.st_mtime,
    }


def read(request: Request) -> str:
    if request.offset < 0 or (request.limit is not None and request.limit < 0):
        raise ValueError("File ranges must be non-negative")
    with opened(request.path) as fd, os.fdopen(os.dup(fd), encoding="utf-8", newline="") as stream:
        remaining = request.offset
        while remaining:
            chunk = stream.read(min(remaining, 65_536))
            if not chunk:
                return ""
            remaining -= len(chunk)
        maximum = request.max_transfer_bytes + 1
        limit = maximum if request.limit is None else min(request.limit, maximum)
        value = stream.read(limit)
        if len(value.encode("utf-8")) > request.max_transfer_bytes:
            raise ValueError("File response exceeds max_transfer_bytes; use a smaller range")
        return value


def write(path: str, text: str, maximum: int) -> None:
    write_bytes(path, text.encode("utf-8"), maximum)


def write_bytes(path: str, data: bytes, maximum: int) -> None:
    if len(data) > maximum:
        raise ValueError("File write exceeds max_transfer_bytes")
    with parent(path, create=True) as (fd, name):
        temporary = f".harnessed-{uuid.uuid4().hex}"
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            mode = 0o600
        else:
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Expected a regular file")
            mode = stat.S_IMODE(info.st_mode) & 0o777
        try:
            target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=fd)
            with os.fdopen(target, "wb") as stream:
                stream.write(data)
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=fd)


def grep(request: Request) -> dict[str, list[str] | int]:
    """Search inside the runtime, with the FilesystemTools preview and result limits."""
    limit = request.limit if request.limit is not None else 100
    if not request.text or not 1 <= limit <= 500:
        raise ValueError("Search requires a non-empty query and a limit in [1, 500]")
    hits: list[str] = []
    notes: list[str] = []
    scanned = 0

    def result() -> dict[str, list[str] | int]:
        value: dict[str, list[str] | int] = {
            "matches": hits,
            "scanned_files": scanned,
            "notes": notes,
        }
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > request.max_transfer_bytes:
            raise ValueError(
                "Search response exceeds max_transfer_bytes; lower limit or narrow query/pattern"
            )
        return value

    paths = _matches(request.path, 1001)
    if len(paths) > 1000:
        notes.append("Path limit reached; narrow the glob pattern to search further.")
    for path in paths[:1000]:
        if metadata(path)["is_dir"]:
            continue
        try:
            # Previews stay in the runtime. Only the bounded result is transferred.
            text = read(
                Request(Operation.READ, path=path, limit=200_001, max_transfer_bytes=4 * 200_001)
            )
        except UnicodeError:
            continue
        scanned += 1
        if len(text) > 200_000:
            notes.append(f"{path}: only the first 200000 characters were searched.")
        for number, line in enumerate(text[:200_000].splitlines(), 1):
            if request.text in line:
                preview = line[:1000] + (" [line truncated]" if len(line) > 1000 else "")
                hits.append(f"{path}:{number}: {preview}")
                if len(hits) == limit:
                    notes.append("Match limit reached; narrow the query or pattern.")
                    return result()
    return result()


def replace(request: Request) -> None:
    if not request.old:
        raise ValueError("The text to replace must not be empty")
    text = read(request)
    count = text.count(request.old)
    if count != 1:
        raise ValueError(f"Expected one match; found {count}")
    write(request.path, text.replace(request.old, request.new, 1), request.max_transfer_bytes)


def delete(request: Request) -> None:
    with parent(request.path) as (fd, name):
        os.unlink(name, dir_fd=fd)


def exists(request: Request) -> bool:
    try:
        metadata(request.path)
    except FileNotFoundError:
        return False
    return True


def list_entries(request: Request) -> list[dict[str, str | int | float | bool]]:
    with opened(request.path, directory=True) as fd:
        # Path.iterdir() cannot list an already opened, confined directory descriptor.
        names = os.listdir(fd)  # noqa: PTH208
    if len(names) > 1000:
        raise ValueError("Directory exceeds 1000 entries; use glob")
    return [metadata(str(PurePosixPath(request.path, name))) for name in sorted(names)]


def glob(request: Request) -> list[str]:
    return _matches(request.path, request.limit if request.limit is not None else 1000)


def read_bytes(request: Request) -> str:
    if request.limit is not None and request.limit < 0:
        raise ValueError("File ranges must be non-negative")
    maximum = request.max_transfer_bytes + 1
    if request.limit is not None:
        maximum = min(maximum, request.limit)
    with opened(request.path) as fd, os.fdopen(os.dup(fd), "rb") as stream:
        data = stream.read(maximum)
    if len(data) > request.max_transfer_bytes:
        raise ValueError("Export exceeds max_transfer_bytes")
    return base64.b64encode(data).decode("ascii")


def import_archive(request: Request) -> None:
    if sys.platform == "win32":
        raise RuntimeError("The sandbox worker must execute inside a POSIX sandbox")
    with opened(".", directory=True):
        pass
    data = base64.b64decode(request.text, validate=True)
    if len(data) > request.max_transfer_bytes:
        raise ValueError("Workspace import exceeds max_transfer_bytes")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        total = 0
        for member in archive:
            parts(member.name)
            if member.isdir():
                with parent(member.name + "/.seed", create=True):
                    pass
                continue
            if not member.isfile():
                raise ValueError("Workspace archives may contain only regular files")
            total += member.size
            if total > request.max_transfer_bytes:
                raise ValueError("Workspace import exceeds max_transfer_bytes")
            stream = archive.extractfile(member)
            assert stream is not None
            with stream:
                write_bytes(member.name, stream.read(), request.max_transfer_bytes)
            with opened(member.name) as fd:
                os.fchmod(fd, member.mode & 0o777)


def _match_path(path: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    if not pattern:
        return not path
    if pattern[0] == "**":
        return _match_path(path, pattern[1:]) or bool(path and _match_path(path[1:], pattern))
    return bool(
        path and fnmatch.fnmatchcase(path[0], pattern[0]) and _match_path(path[1:], pattern[1:])
    )


def _matches(pattern: str, limit: int) -> list[str]:
    if sys.platform == "win32":
        raise RuntimeError("The sandbox worker must execute inside a POSIX sandbox")
    parts(pattern)
    if limit < 1:
        raise ValueError("Glob limit must be positive")
    result: list[str] = []
    with opened(".", directory=True) as fd:
        for root, directories, files, root_fd in os.fwalk(".", dir_fd=fd, follow_symlinks=False):
            directories[:] = sorted(
                name
                for name in directories
                if stat.S_ISDIR(os.stat(name, dir_fd=root_fd, follow_symlinks=False).st_mode)
            )
            for name in sorted([*directories, *files]):
                info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    continue
                path = PurePosixPath(root, name)
                if _match_path(path.parts, PurePosixPath(pattern).parts):
                    result.append(path.as_posix())
                    if len(result) == limit:
                        return result
    return result
