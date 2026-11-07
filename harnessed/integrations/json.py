import hashlib
import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from functools import partial
from pathlib import Path

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from harnessed.core.errors import SessionFormatError, SessionWriteConflict
from harnessed.core.session.checkpoint import SessionCheckpoint
from harnessed.core.session.commit import SessionCommit
from harnessed.core.session.store import StoredSession
from harnessed.kernel import run_sync

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class JsonSessionStore:
    """Opt-in atomic JSON checkpoints, with compare-and-set across local processes.

    The directory is created on the first save. IDs are hashed into filenames;
    application identifiers need not be valid filesystem paths. Locks cover
    revision checking and replacement, not the lifetime of an agent execution.
    Use a local filesystem with OS locking and atomic replacement support.
    """

    def __init__(self, *, root: str | Path) -> None:
        self._root = Path(root).resolve()

    async def load_session(self, conversation_id: str) -> StoredSession | None:
        return await run_sync(partial(self._load, conversation_id))

    async def save_session(
        self,
        conversation_id: str,
        commit: SessionCommit,
        *,
        expected_revision: int | None,
    ) -> int:
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 1
        ):
            raise ValueError("Expected revisions must be positive integers or None")
        if not isinstance(commit, SessionCommit):
            raise TypeError("Session stores require a SessionCommit")
        return await run_sync(
            partial(self._save, conversation_id, commit.checkpoint, expected_revision)
        )

    def _path(self, conversation_id: str) -> Path:
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("Conversation IDs must be non-empty strings")
        key = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
        return self._root / f"{key}.json"

    def _load(self, conversation_id: str) -> StoredSession | None:
        path = self._path(conversation_id)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            record = _RECORD.validate_json(data)
            if type(record.version) is not int or record.conversation_id != conversation_id:
                raise ValueError("Session identity or version does not match")
        except (ValueError, ValidationError) as error:
            raise SessionFormatError(f"Invalid JSON session checkpoint: {path.name}") from error
        return StoredSession(record.checkpoint, record.revision)

    def _save(
        self,
        conversation_id: str,
        checkpoint: SessionCheckpoint,
        expected_revision: int | None,
    ) -> int:
        path = self._path(conversation_id)
        self._root.mkdir(parents=True, exist_ok=True)
        with _writer_lock(path.with_suffix(".lock")):
            current = self._load(conversation_id)
            revision = current.revision if current is not None else None
            if revision != expected_revision:
                raise SessionWriteConflict(conversation_id)
            next_revision = 1 if revision is None else revision + 1
            data = _RECORD.dump_json(_Record(conversation_id, checkpoint, next_revision), indent=2)
            descriptor, name = tempfile.mkstemp(prefix=f".{path.stem}-", dir=self._root)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                if os.name != "nt":
                    directory = os.open(self._root, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)
            return next_revision


@contextmanager
def _writer_lock(path: Path) -> Generator[None]:
    # Lock files remain in place: unlinking a lock can let writers lock different inodes.
    with path.open("a+b") as stream:
        if os.name == "nt":
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True, config=ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True))
class _Record:
    conversation_id: str
    checkpoint: SessionCheckpoint
    revision: int = Field(ge=1)
    version: int = Field(default=3, ge=3, le=3)


_RECORD = TypeAdapter(_Record)

__all__ = ["JsonSessionStore"]
