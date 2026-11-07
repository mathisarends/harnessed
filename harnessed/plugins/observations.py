from harnessed.core.events.observations import ObservationPolicyAppliedEvent
from harnessed.core.plugin import Plugin
from harnessed.core.session.session import SessionLog
from harnessed.kernel import needs


class LatestObservationOnly(Plugin):
    """Explicitly retain only the latest unchanged observation per source.

    Replacing a snapshot changes the request prefix. Omit this plugin to retain
    snapshots until compaction and preserve their text and images together.
    """

    session = needs(SessionLog)

    _previous: int | None

    async def setup(self) -> None:
        previous = self.session.last(ObservationPolicyAppliedEvent)
        self._previous = previous.max_observations if previous is not None else None
        await self.session.append(ObservationPolicyAppliedEvent(1))

    async def teardown(self) -> None:
        await self.session.append(ObservationPolicyAppliedEvent(self._previous))
