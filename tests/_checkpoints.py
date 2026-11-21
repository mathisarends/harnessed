from harnessed.events import ControlState, SessionControlChangedEvent
from harnessed.sessions import SessionCheckpoint, SessionCommit, SessionState


def checkpoint(marker: str = "") -> SessionCheckpoint:
    return SessionCheckpoint(
        state=SessionState(plugin_values={"marker": marker}),
        offset=0,
        control=SessionControlChangedEvent(ControlState.ACTIVE),
    )


def commit(marker: str = "") -> SessionCommit:
    return SessionCommit(checkpoint(marker))
