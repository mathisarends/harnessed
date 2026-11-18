from typing import Annotated

from pydantic import Field

from harnessed.kernel import Emit


class SessionEvent(Emit):
    @property
    def type(self) -> str:
        return type(self).__name__


class DurableEvent(SessionEvent):
    """A persisted event, built-in or application-defined, replayed with the log."""


type Identifier = Annotated[str, Field(min_length=1)]


type StepNumber = Annotated[int, Field(ge=1)]


class TransientEvent[T](SessionEvent):
    """A live notification, never persisted; T preserves a stream's result type.

    Ordinary notifications need no type argument. Terminal result notifications
    bind T so narrowing an open stream retains the application's output type.
    """
