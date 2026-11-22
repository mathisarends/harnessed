from harnessed.events import ControlState, SessionControlChangedEvent
from harnessed.sessions import SessionCheckpoint, SessionState


def checkpoint(marker: str = "") -> SessionCheckpoint:
    return SessionCheckpoint(
        state=SessionState(plugin_values={"marker": marker}),
        offset=0,
        control=SessionControlChangedEvent(ControlState.ACTIVE),
    )
