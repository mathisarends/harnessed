from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConversationRow:
    """Example application-owned record; it has no SDK dependency.

    ``document`` stands in for a JSONB column and ``format_version`` for a
    plain integer column that migrations and queries can use.
    """

    document: dict[str, Any]
    format_version: int
    retained_messages: int


class ConversationRepository:
    """Offline stand-in for an existing repository, such as a Postgres table."""

    def __init__(self) -> None:
        self._rows: dict[str, ConversationRow] = {}

    async def fetch(self, key: str) -> ConversationRow | None:
        return self._rows.get(key)

    async def save(
        self,
        key: str,
        *,
        document: dict[str, Any],
        format_version: int,
        retained_messages: int,
    ) -> None:
        self._rows[key] = ConversationRow(document, format_version, retained_messages)
