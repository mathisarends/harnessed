from typing import Annotated

from pydantic import Field

from harnessed.kernel import Emit


class SessionEvent(Emit):
    @property
    def type(self) -> str:
        return type(self).__name__


class DurableEvent(SessionEvent):
    """Extension point for application data that survives session replay."""


type Identifier = Annotated[str, Field(min_length=1)]


type StepNumber = Annotated[int, Field(ge=1)]


class TransientEvent(Emit):
    """A live notification; never accepted by Session.append or the log codec."""

    @property
    def type(self) -> str:
        return type(self).__name__


class CustomTransientEvent(TransientEvent):
    """Extension point for live application notifications."""
