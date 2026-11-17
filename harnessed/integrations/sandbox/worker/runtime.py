import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict
from pathlib import PurePosixPath

from . import filesystem
from .models import CommandResult, Operation, Request


def command(request: Request) -> CommandResult:
    if sys.platform == "win32":
        raise RuntimeError("The sandbox worker must execute inside a POSIX sandbox")
    if not request.argv or request.timeout <= 0 or request.max_output_bytes < 256:
        raise ValueError("Invalid command arguments or limits")
    components = filesystem.parts(request.path)
    process = subprocess.Popen(
        request.argv,
        cwd=PurePosixPath("/workspace", *components),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    captures = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    budget = request.max_output_bytes // 2
    deadline = time.monotonic() + request.timeout
    timed_out = False

    def kill() -> None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                if time.monotonic() >= deadline:
                    timed_out = True
                    kill()
                    break
                if process.poll() is not None:
                    kill()
                for key, _ in selector.select(timeout=0.05):
                    chunk = os.read(key.fd, 65_536)
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    name = key.data
                    totals[name] += len(chunk)
                    captures[name].extend(chunk[: max(0, budget - len(captures[name]))])
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()

    def text(name: str) -> str:
        data = captures[name]
        value = data.decode("utf-8", errors="replace")
        if totals[name] > len(data):
            value += f"\n[{name} truncated: kept {len(data)} of {totals[name]} bytes.]"
        return value

    return CommandResult(process.returncode, text("stdout"), text("stderr"), timed_out)


def response(payload: str) -> str:
    try:
        data = json.loads(payload)
        data["operation"] = Operation(data["operation"])
        request = Request(**data)
        value = _dispatch(request)
        response = {"value": value, "error": None, "error_type": None}
    except (OSError, ValueError, UnicodeError) as error:
        response = {"value": None, "error": str(error), "error_type": type(error).__name__}
    return json.dumps(response, ensure_ascii=True)


def main() -> None:
    if sys.version_info < (3, 12):  # noqa: UP036 -- Executed in application-supplied images.
        raise RuntimeError("Sandbox helpers require Python 3.12+")
    if "--serve" in sys.argv:
        print('{"ready":true}', flush=True)
        for line in sys.stdin:
            print(response(line), flush=True)
    else:
        print(response(sys.stdin.read()), flush=True)


def _dispatch(request: Request) -> object:
    if sys.platform == "win32":
        raise RuntimeError("The sandbox worker must execute inside a POSIX sandbox")
    if request.version != 1:
        raise ValueError("Unsupported sandbox protocol version")
    handler = _HANDLERS.get(request.operation)
    if handler is None:
        raise ValueError("Unknown sandbox operation")
    return handler(request)


def _run(request: Request) -> dict[str, object]:
    return asdict(command(request))


def _write(request: Request) -> None:
    filesystem.write(request.path, request.text, request.max_transfer_bytes)


def _stat(request: Request) -> dict[str, str | int | float | bool]:
    return filesystem.metadata(request.path)


_HANDLERS: dict[Operation, Callable[[Request], object]] = {
    Operation.RUN: _run,
    Operation.READ: filesystem.read,
    Operation.WRITE: _write,
    Operation.REPLACE: filesystem.replace,
    Operation.DELETE: filesystem.delete,
    Operation.EXISTS: filesystem.exists,
    Operation.STAT: _stat,
    Operation.LIST: filesystem.list_entries,
    Operation.GLOB: filesystem.glob,
    Operation.GREP: filesystem.grep,
    Operation.READ_BYTES: filesystem.read_bytes,
    Operation.IMPORT: filesystem.import_archive,
}
