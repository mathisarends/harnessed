from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harnessed.core.events.base import TransientEvent
from harnessed.kernel import Emit

if TYPE_CHECKING:
    from harnessed.core.session.session import SessionLog


@dataclass(frozen=True, slots=True)
class SessionPublished(Emit):
    log: SessionLog
    event: TransientEvent
