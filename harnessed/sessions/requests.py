from harnessed.events import (
    AssistantMessageEvent,
    DurableEvent,
    ModelRequestedEvent,
    ModelRequestFailedEvent,
    ModelUsageRecordedEvent,
    ToolExecutionFinishedEvent,
    ToolExecutionStartedEvent,
)
from harnessed.models import ModelRequest, ModelUsage
from harnessed.models.model import sum_usage


class RequestProjection:
    """Track current-turn model requests, open tool executions and session usage.

    Requests are released with their turn; open executions and usage persist.
    """

    def __init__(self) -> None:
        self._requests: list[ModelRequest] = []
        self._by_id: dict[str, ModelRequest] = {}
        self._finished: set[str] = set()
        self._open_executions: set[str] = set()
        self._snapshot: tuple[ModelRequest, ...] | None = None
        self._usage: ModelUsage | None = None

    @property
    def requests(self) -> tuple[ModelRequest, ...]:
        if self._snapshot is None:
            self._snapshot = tuple(self._requests)
        return self._snapshot

    @property
    def usage(self) -> ModelUsage:
        return self._usage if self._usage is not None else ModelUsage(0)

    def request(self, request_id: str) -> ModelRequest | None:
        return self._by_id.get(request_id)

    def finished(self, request_id: str) -> bool:
        return request_id in self._finished

    def unfinished(self) -> tuple[str, ...]:
        return tuple(request_id for request_id in self._by_id if request_id not in self._finished)

    def execution_pending(self, call_id: str) -> bool:
        return call_id in self._open_executions

    def apply(self, event: DurableEvent) -> None:
        if isinstance(event, ToolExecutionStartedEvent):
            self._open_executions.add(event.call_id)
        elif isinstance(event, ToolExecutionFinishedEvent):
            self._open_executions.discard(event.call_id)
        elif isinstance(event, ModelRequestedEvent):
            self._requests.append(event.request)
            self._by_id[event.request_id] = event.request
            self._snapshot = None
        elif isinstance(event, AssistantMessageEvent | ModelRequestFailedEvent):
            self._finished.add(event.request_id)
        elif isinstance(event, ModelUsageRecordedEvent):
            self._usage = (
                event.usage if self._usage is None else sum_usage((self._usage, event.usage))
            )

    def release_turn(self) -> None:
        self._requests.clear()
        self._by_id.clear()
        self._finished.clear()
        self._snapshot = None
