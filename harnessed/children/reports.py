from typing import Annotated

from pydantic import Field

from harnessed.runtime.plugin import Plugin
from harnessed.tools import tool

from .contracts import ChildStatusReporter


class ChildReports(Plugin):
    """Mounted only for parent-owned children; replies never complete their task."""

    def __init__(self, reporter: ChildStatusReporter) -> None:
        super().__init__()
        self.reporter = reporter

    @tool(readonly=True)
    async def answer_status(
        self,
        request_id: str,
        message: Annotated[str, Field(min_length=1, max_length=8192)],
    ) -> str:
        """Answer a pending parent status request, then continue the original task."""
        await self.reporter.answer(request_id, message)
        return "Status delivered. Continue your original task; this is not its final answer."
