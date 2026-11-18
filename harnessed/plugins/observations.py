from typing import override

from harnessed.events import ObservationPolicyAppliedEvent
from harnessed.kernel import PluginScope, needs
from harnessed.runtime.plugin import Plugin
from harnessed.sessions.log import SessionLog


class LatestObservationOnly(Plugin):
    """Explicitly retain only the latest unchanged observation per source.

    Replacing a snapshot changes the request prefix. Omit this plugin to retain
    snapshots until compaction and preserve their text and images together.
    """

    session = needs(SessionLog)

    _previous: int | None

    @override
    async def setup(self, scope: PluginScope) -> None:
        previous = self.session.last(ObservationPolicyAppliedEvent)
        self._previous = previous.max_observations if previous is not None else None
        await self.session.append(ObservationPolicyAppliedEvent(1))

    @override
    async def teardown(self, scope: PluginScope) -> None:
        await self.session.append(ObservationPolicyAppliedEvent(self._previous))
