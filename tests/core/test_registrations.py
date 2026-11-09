from collections.abc import Callable

import pytest

from harnessed import Agent, Plugin, tool
from harnessed.kernel import Scope, registration
from harnessed.testing import ScriptedModel, call


@pytest.mark.parametrize("outer", [True, False])
async def test_custom_registrations_compose_with_native_tools(outer: bool) -> None:
    callbacks: list[Callable[..., object]] = []
    cleaned: list[Callable[..., object]] = []

    class Binding:
        needs = ()

        def mount(self, scope: Scope, callback: Callable[..., object]) -> None:
            callbacks.append(callback)
            scope.effect(lambda: cleaned.append(callback))

    def declare[F: Callable[..., object]](callback: F) -> F:
        attach = registration(Binding())
        native = tool(readonly=True)
        return attach(native(callback)) if outer else native(attach(callback))

    class Feature(Plugin, provides=True):
        @declare
        async def echo(self, value: str) -> str:
            return value

    model = ScriptedModel([call("echo", value="bound"), "finished"])
    recipe = Feature()
    async with Agent(model=model, plugins=[recipe]).session() as session:
        mounted = session.scope[Feature]
        assert callbacks == [mounted.echo]
        assert mounted is not recipe
        assert await mounted.echo("direct") == "direct"
        result = await session.run("Echo")
        assert result.text == "finished"
        assert result.tool_calls[0][1].text == "bound"
        assert session.tools["echo"].parameters["properties"]["value"]["type"] == "string"
    assert cleaned == callbacks
