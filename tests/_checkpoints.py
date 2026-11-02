from harnessed import SessionState
from harnessed.events import AgentControlChangedEvent, ControlState
from harnessed.sessions import SessionCheckpoint, SessionCommit


def checkpoint(marker: str = "") -> SessionCheckpoint:
    return SessionCheckpoint(
        state=SessionState(context={"marker": marker}),
        offset=0,
        control=AgentControlChangedEvent(ControlState.ACTIVE),
    )


def commit(marker: str = "") -> SessionCommit:
    return SessionCommit(checkpoint(marker))
