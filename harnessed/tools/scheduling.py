import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterable, Iterator
from typing import Annotated

from pydantic import AfterValidator
from pydantic.dataclasses import dataclass

from harnessed.models import ToolCall, ToolResult
from harnessed.models.values import VALUE_CONFIG, NonEmptyText

from .info import ToolEnvelope, ToolInfo
from .policy import OutputLimit, OutputLimitSetting

type ResourceSpec = tuple[str, ...] | Callable[[ToolCall], Iterable[str]]


def _distinct(keys: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(keys))


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolScheduling:
    """Whether a tool may run in parallel and the named resources it locks."""

    parallel: bool = False
    resources: Annotated[tuple[NonEmptyText, ...], AfterValidator(_distinct)] = ()


def resource_spec(resources: ResourceSpec) -> ResourceSpec:
    if callable(resources):
        if inspect.iscoroutinefunction(resources):
            raise TypeError("Resource resolvers must be synchronous")
        return resources
    return ToolScheduling(resources=resources).resources


@dataclass(frozen=True)
class PreparedToolCall:
    call: ToolCall
    scheduling: ToolScheduling
    error: ToolResult | None = None
    grant_key: str | None = None
    binding: object | None = None
    output_limit: OutputLimitSetting = OutputLimit.INHERIT
    tool: ToolInfo | None = None
    envelope: ToolEnvelope = ToolEnvelope()


class ToolScheduler:
    def __init__(self, max_parallel: int) -> None:
        self.max_parallel = max_parallel
        self.slots = asyncio.Semaphore(max_parallel)

    def groups(self, plans: Iterable[PreparedToolCall]) -> Iterator[tuple[PreparedToolCall, ...]]:
        pending = []
        for plan in plans:
            if self.max_parallel > 1 and plan.scheduling.parallel:
                pending.append(plan)
            else:
                if pending:
                    yield tuple(pending)
                    pending.clear()
                yield (plan,)
        if pending:
            yield tuple(pending)

    async def run(
        self,
        plans: tuple[PreparedToolCall, ...],
        worker: Callable[[int, PreparedToolCall], Awaitable[None]],
    ) -> None:
        if len(plans) == 1:
            await worker(0, plans[0])
            return

        async def invoke(index, plan, predecessors):
            for predecessor in predecessors:
                await asyncio.shield(predecessor)
            return await worker(index, plan)

        resources: dict[str, asyncio.Task[None]] = {}
        async with asyncio.TaskGroup() as group:
            for index, plan in enumerate(plans):
                predecessors = tuple(
                    dict.fromkeys(
                        resources[key] for key in plan.scheduling.resources if key in resources
                    )
                )
                task = group.create_task(
                    invoke(index, plan, predecessors), name=f"harnessed:tool:{plan.call.id}"
                )
                for key in plan.scheduling.resources:
                    resources[key] = task
