from harnessed.events import ControlState, SessionControlChangedEvent
from harnessed.sessions import SessionRecovery, SessionSnapshot, SessionState


def snapshot(marker: str = "", *, sequence: int = 0) -> SessionSnapshot:
    return SessionSnapshot(
        session_id="session",
        sequence=sequence,
        state=SessionState(plugin_values={"marker": marker}),
        control=SessionControlChangedEvent(ControlState.ACTIVE),
    )


def recovery(marker: str = "") -> SessionRecovery:
    return SessionRecovery(snapshot(marker))
