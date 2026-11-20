"""Run with: uv run python -m examples.plugin_authoring.11_observations (in-memory browser)."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from harnessed import Agent, Plugin, tool
from harnessed.hooks import Next, PreStep, StepDecision
from harnessed.kernel import PluginScope, hook, needs
from harnessed.models import ImagePart, ModelChunk, TextPart, ToolCall, UserMessage
from harnessed.testing import ScriptedModel


@dataclass(frozen=True)
class PageState:
    url: str
    title: str

    def render(self) -> str:
        return f"Page: {self.title} ({self.url})\n[settings] button Settings\n[home] link Home"


class Browser(Protocol):
    @property
    def current_url(self) -> str: ...

    async def snapshot(self) -> PageState: ...

    async def click(self, ref: str) -> None: ...


class DemoBrowser:
    def __init__(self):
        self.current_url = "https://example.test/"
        self.title = "Home"

    async def snapshot(self) -> PageState:
        return PageState(self.current_url, self.title)

    async def click(self, ref: str) -> None:
        if ref != "settings":
            raise ValueError(f"Unknown ref: {ref}")
        self.current_url = "https://example.test/settings"
        self.title = "Settings"


class BrowserBackend(Plugin):
    async def setup(self, scope: PluginScope) -> None:
        scope.provide(Browser, DemoBrowser())


class BrowserObserver(Plugin):
    browser = needs(Browser)

    @hook(PreStep)
    async def observe(
        self,
        event: PreStep,
        next_: Next[StepDecision],
    ) -> StepDecision:
        state = await self.browser.snapshot()
        event.session.observe(
            UserMessage(
                (
                    TextPart(state.render()),
                    # ScriptedModel records this reference without downloading it.
                    ImagePart(f"https://example.test/screenshots/{state.title.lower()}.png"),
                )
            ),
            source="browser",
            refs=(state.url,),
        )
        return await next_()


class BrowserTools(Plugin):
    browser = needs(Browser)

    @tool
    async def click(self, ref: str) -> str:
        """Click an element by its ref from the current page state."""
        before = self.browser.current_url
        await self.browser.click(ref)
        after = self.browser.current_url
        return f"Clicked {ref}." + (f" Navigated to {after}." if after != before else "")

    @tool
    async def snapshot(self) -> str:
        """Get the full current page state if the page may have changed."""
        return (await self.browser.snapshot()).render()


browser_tools = BrowserTools()


async def main():
    model = ScriptedModel(
        [[ModelChunk(tool_call=ToolCall("click", {"ref": "settings"}))], "Settings opened."]
    )
    agent = Agent(
        model=model,
        plugins=[BrowserBackend(), BrowserObserver(), browser_tools],
    )
    async with agent.session() as session:
        print((await session.run("Open Settings.")).text)
        for index, request in enumerate(model.requests, 1):
            print(f"\nRequest {index}:")
            for message in request.messages:
                if message.id.startswith("observation:"):
                    print(message.text)


if __name__ == "__main__":
    asyncio.run(main())
