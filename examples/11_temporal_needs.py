"""Run with: uv run python -m examples.11_temporal_needs"""

import asyncio
from dataclasses import dataclass

from harnessed import Plugin, ToolCall, tool
from harnessed.kernel import Emit, PluginScope, Scope, needs
from harnessed.services import SystemPromptStore, ToolRegistry


@dataclass
class Browser:
    name: str

    async def title(self) -> str:
        return f"Title from {self.name}"


class PageChanged(Emit):
    pass


class BrowserTools(Plugin):
    browser = needs(Browser)
    prompt = needs(SystemPromptStore)

    async def setup(self, scope: PluginScope) -> None:
        print(f"Setup with {self.browser.name}")
        self.prompt.fixed(f"Current browser: {self.browser.name}")
        scope.on(PageChanged, lambda event: print(f"Listener with {self.browser.name}"))
        scope.effect(lambda: print(f"Custom effect cleaned for {self.browser.name}"))

    @tool(readonly=True)
    async def title(self) -> str:
        """Read the current page title."""
        return await self.browser.title()

    async def teardown(self, scope: PluginScope) -> None:
        print(f"Teardown with {self.browser.name}")


async def main():
    async with Scope() as scope:
        scope.provide(ToolRegistry, ToolRegistry(scope))
        scope.provide(SystemPromptStore, SystemPromptStore(scope))
        feature = await scope.plugin(BrowserTools())
        print("Before browser:", feature.status)

        remove = scope.provide(Browser, Browser("local browser"))
        await scope.settle()
        print("Available:", feature.status)
        print((await scope[ToolRegistry].execute(ToolCall("title"))).content)
        print("Prompt:", await scope[SystemPromptStore].render())
        await scope.dispatch(PageChanged())

        await remove()
        await scope.settle()
        print("After service removal:", feature.status)
        print("Visible tools:", [t.name for t in scope[ToolRegistry].definitions])
        print("Prompt after removal:", repr(await scope[SystemPromptStore].render()))
        await scope.dispatch(PageChanged())

        scope.provide(Browser, Browser("replacement browser"))
        await scope.settle()
        print("Recomposed:", feature.status)
        print((await scope[ToolRegistry].execute(ToolCall("title"))).content)
        print("Fresh prompt:", await scope[SystemPromptStore].render())
        await scope.dispatch(PageChanged())

        # Permanent removal: this recipe will no longer restart.
        await feature.aclose()
        print("Explicitly disposed:", feature.status)


if __name__ == "__main__":
    asyncio.run(main())
