# Harnessed

[![PyPI version](https://img.shields.io/pypi/v/harnessed?style=for-the-badge&logo=pypi&logoColor=white&color=3776AB)](https://pypi.org/project/harnessed/)
[![Python versions](https://img.shields.io/pypi/pyversions/harnessed?style=for-the-badge&logo=python&logoColor=white&color=3776AB)](https://pypi.org/project/harnessed/)
[![CI status](https://img.shields.io/github/actions/workflow/status/mathisarends/harnessed/tests.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=CI)](https://github.com/mathisarends/harnessed/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge)](https://github.com/mathisarends/harnessed/blob/main/LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-f59e0b?style=for-the-badge)](https://github.com/mathisarends/harnessed/blob/main/docs/releases.md)

A composable Python agent SDK. Keep tools, instructions and event hooks together
in plugins, combine them into reusable packs, and stream typed events from a live
session. Harnessed owns the agent loop and plugin lifetimes.

Python **3.12-3.14**, Pydantic v2. Optional provider integrations use PydanticAI's
Direct API.

## Build an agent

```bash
uv add "harnessed[openai]"
```

```python
import asyncio

from harnessed import Agent, Plugin, SystemPrompt, tool
from harnessed.events import ToolCallFinishedEvent
from harnessed.kernel import hook


class Calculator(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt("Use add for arithmetic instead of calculating mentally.")

    @tool(readonly=True)
    async def add(self, a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @hook(ToolCallFinishedEvent)
    def on_result(self, event: ToolCallFinishedEvent) -> None:
        print(f"{event.call.name}: {event.result.status}")


calculator = Calculator()
agent = Agent(
    model="deepseek:deepseek-chat",  # DEEPSEEK_API_KEY
    plugins=[calculator],
    instructions="You are a helpful assistant. Keep answers concise.",
)


async def main() -> None:
    result = await agent.run("What is 19 + 23?")
    print(result.text)


asyncio.run(main())
```

One plugin colocates the tool, its model-facing instructions and its event hook.
`@tool` derives the input schema from Python types; the method docstring describes
the tool. `@hook` observes the selected event type across the session.
The agent appends each plugin's instruction block to its own instructions, in
plugin-list order; `print(agent.prompt)` shows every section and its origin.

`Agent` is reusable configuration. `agent.run()` opens a session for one turn;
use `agent.session()` when several turns should share a conversation.

Select a model with a `provider:model` string, such as
`Agent(model="openai-responses:gpt-6.1-terra")` (`OPENAI_API_KEY`). Harnessed
configures the PydanticAI adapter automatically. Use an explicit model object
when you need generation defaults or native provider configuration; see
[model configuration](https://github.com/mathisarends/harnessed/blob/main/docs/guides/models.md#choose-a-model).

## Keep a conversation open

Use one session for shared history and typed streaming events:

```python
from harnessed.events import TextDeltaEvent


async def conversation() -> None:
    async with agent.session() as session:
        async with session.stream("What is 19 + 23?") as run:
            async for event in run:
                if isinstance(event, TextDeltaEvent):
                    print(event.text, end="", flush=True)
            result = await run.result()

        follow_up = await session.run("Add 8 to that result.")
        print(follow_up.text)
```

Sessions own live plugin resources and conversation state. Export `result.state`
for application-owned persistence, or configure a session store to save automatically.
See [sessions](https://github.com/mathisarends/harnessed/blob/main/docs/guides/sessions.md)
and [streaming](https://github.com/mathisarends/harnessed/blob/main/docs/guides/streaming.md)
for restoration, cancellation and approvals.

## Explore the SDK

| Task | Guide |
| --- | --- |
| Configure providers and generation settings | [Models](https://github.com/mathisarends/harnessed/blob/main/docs/guides/models.md) |
| Compose packs, inject services or mount temporary tools | [Plugins](https://github.com/mathisarends/harnessed/blob/main/docs/guides/plugins.md) |
| Define tools, deadlines and policies | [Tools](https://github.com/mathisarends/harnessed/blob/main/docs/guides/tools.md) |
| Validate typed answers | [Structured output](https://github.com/mathisarends/harnessed/blob/main/docs/guides/structured-output.md) |
| Test without credentials | [Testing](https://github.com/mathisarends/harnessed/blob/main/docs/guides/testing.md) |
| Connect MCP, subagents, sandboxes or tracing | [Documentation index](https://github.com/mathisarends/harnessed/blob/main/docs/index.md) |

The [SDK reference](https://github.com/mathisarends/harnessed/blob/main/docs/reference/sdk.md)
covers the public API; [executable examples](https://github.com/mathisarends/harnessed/blob/main/docs/guides/examples.md)
show complete offline compositions. The standalone `harnessed.kernel` uses only
the standard library.

Harnessed 0.x is alpha software. Read the
[compatibility policy](https://github.com/mathisarends/harnessed/blob/main/docs/releases.md)
and [changelog](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)
before upgrading. For contributions and release checks, see
[development](https://github.com/mathisarends/harnessed/blob/main/docs/contributing/development.md).

Inspired by [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
and [Cordis](https://github.com/cordiverse/cordis).
Released under the [MIT License](https://github.com/mathisarends/harnessed/blob/main/LICENSE).
