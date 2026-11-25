from harnessed.events import ControlState, SessionControlChangedEvent
from harnessed.sessions import SessionBoundary, SessionCheckpoint, SessionState


def boundary(marker: str = "", *, sequence: int = 0) -> SessionBoundary:
    return SessionBoundary(
        session_id="session",
        sequence=sequence,
        state=SessionState(plugin_values={"marker": marker}),
        control=SessionControlChangedEvent(ControlState.ACTIVE),
    )


def checkpoint(marker: str = "") -> SessionCheckpoint:
    return SessionCheckpoint(boundary(marker))
