from typing import assert_type

from harnessed import Plugin, tool
from harnessed.core.tools.registry import ToolRegistry


class TimedTools(Plugin):
    def __init__(self, *, wait_seconds: float = 120) -> None:
        super().__init__()
        self.wait_seconds = wait_seconds

    def _timeout(self) -> float:
        return self.wait_seconds + 60

    @tool(timeout_seconds=_timeout)
    async def wait(self, reason: str) -> str:
        return reason

    @tool(timeout_seconds=lambda self: self.wait_seconds)
    def poll(self) -> bool:
        return True

    @tool(timeout_seconds=None)
    def inherited(self) -> int:
        return 1


async def check_timeouts(registry: ToolRegistry, tools: TimedTools) -> None:
    @registry.tool(timeout_seconds=120)
    async def standalone() -> str:
        return "done"

    registry.register(standalone, name="direct", timeout_seconds=60)
    assert_type(await tools.wait("ready"), str)
    assert_type(tools.poll(), bool)
    assert_type(tools.inherited(), int)
    assert_type(await standalone(), str)
