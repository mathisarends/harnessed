from dataclasses import dataclass

from harnessed.kernel import Scope
from harnessed.models import Model, ModelRequest
from harnessed.sessions.log import SessionLog

from .budget import ContextBudget
from .tokens import TokenCounter, count_request


@dataclass(frozen=True)
class ContextState:
    """How full the model context is for one request.

    ``context_window`` is the effective limit: the model window capped by
    ``Context(max_tokens=...)``, or that cap alone when the model window is unknown.
    """

    input_tokens: int
    reserve_tokens: int
    context_window: int | None
    turns_since_compaction: int

    @property
    def ratio(self) -> float | None:
        """Trigger occupancy includes the output reserve; event occupancy is input only."""
        if self.context_window is None:
            return None
        return (self.input_tokens + self.reserve_tokens) / self.context_window


async def measure_context(
    request: ModelRequest, *, model: Model, counter: TokenCounter, scope: Scope, log: SessionLog
) -> ContextState:
    """Count a request against the session's context budget and the model window."""
    budget = scope.get(ContextBudget, ContextBudget())
    input_tokens = await count_request(counter, request)
    return ContextState(
        input_tokens,
        budget.output_reserve(request),
        budget.window_limit(model.info.context_window),
        log.uncompacted_turn_count,
    )


class ContextMeter:
    """Measure how full the context of this session's model would be for a request.

    Plugins depend on it with ``needs(ContextMeter)``. Counting can call the
    provider, so measure only the requests whose occupancy a plugin needs.
    Compaction triggers receive the same measurement.
    """

    def __init__(self, scope: Scope, log: SessionLog) -> None:
        self._scope = scope
        self._log = log

    async def measure(self, request: ModelRequest) -> ContextState:
        """Return input tokens, output reserve and effective window for ``request``."""
        return await measure_context(
            request,
            model=self._scope[Model],
            counter=self._scope[TokenCounter],
            scope=self._scope,
            log=self._log,
        )
