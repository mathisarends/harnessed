from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationRow:
    """Example application-owned record; it has no SDK dependency.

    ``snapshot`` and each ``journal`` document stand in for JSONB columns, and
    ``format_version`` for a plain integer column that queries can use.
    """

    snapshot: dict[str, Any]
    format_version: int
    retained_messages: int
    journal: dict[int, dict[str, Any]] = field(default_factory=dict)


class ConversationRepository:
    """Offline stand-in for an existing repository, such as two Postgres tables."""

    def __init__(self) -> None:
        self._rows: dict[str, ConversationRow] = {}
        self._journals: dict[str, dict[int, dict[str, Any]]] = {}

    async def fetch(self, key: str) -> ConversationRow | None:
        return self._rows.get(key)

    async def save(
        self,
        key: str,
        *,
        journal: dict[int, dict[str, Any]],
        snapshot: dict[str, Any] | None,
        format_version: int,
        retained_messages: int,
    ) -> None:
        stored = self._journals.setdefault(key, {})
        for sequence, document in journal.items():
            # A redelivered entry is already stored and stays unchanged.
            stored.setdefault(sequence, document)
        if snapshot is not None:
            self._rows[key] = ConversationRow(snapshot, format_version, retained_messages, stored)
