from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationRow:
    """Example application-owned record; it has no SDK dependency."""

    body: bytes
    version: int
    retained_messages: int


class ConversationConflictError(RuntimeError):
    pass


class ConversationRepository:
    """Offline stand-in for an existing repository and its atomic transaction."""

    def __init__(self) -> None:
        self._rows: dict[str, ConversationRow] = {}

    async def fetch(self, key: str) -> ConversationRow | None:
        return self._rows.get(key)

    async def commit(
        self,
        key: str,
        *,
        body: bytes,
        retained_messages: int,
        expected_version: int | None,
    ) -> int:
        current = self._rows.get(key)
        version = current.version if current is not None else None
        if version != expected_version:
            raise ConversationConflictError(key)
        next_version = 1 if version is None else version + 1
        # No await between comparison and replacement: atomic in this single-loop fake.
        # A real repository performs this comparison and write in its DB transaction.
        self._rows[key] = ConversationRow(body, next_version, retained_messages)
        return next_version
