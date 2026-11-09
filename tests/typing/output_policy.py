from typing import assert_type

from harnessed import Plugin, hook
from harnessed.hooks import Next, OutputDecision, PrepareOutput
from harnessed.models import ModelRequest, OutputSchema


class Completion(Plugin):
    @hook(PrepareOutput)
    async def output(self, event: PrepareOutput, next_: Next[OutputDecision]) -> OutputDecision:
        assert_type(event.request, ModelRequest)
        assert_type(event.schema, OutputSchema | None)
        assert_type(await next_(), OutputDecision)
        return OutputDecision.DEFER
