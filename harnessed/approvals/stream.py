from harnessed.events import InboxAddedEvent

from .session import LiveResponder


class StreamApprovals(LiveResponder):
    """Only the stream owning this inbox item can answer its turn's approvals."""

    input_id: str | None = None

    async def __aexit__(self, *exc: object) -> None:
        await super().__aexit__(*exc)
        if self.input_id is not None:
            self._live.release(self.input_id)
        self.hand_over("The approval stream was closed")

    def prepared(self, item: InboxAddedEvent) -> None:
        self.input_id = item.message.id
        self._live.own(self.input_id)

    def answers(self) -> bool:
        return self.input_id is not None and self.open_turn_input() == self.input_id
