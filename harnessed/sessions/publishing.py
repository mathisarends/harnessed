from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harnessed.events import TransientEvent
from harnessed.kernel import Emit

if TYPE_CHECKING:
    from .log import SessionLog


@dataclass(frozen=True, slots=True)
class SessionPublished(Emit):
    log: SessionLog
    event: TransientEvent
