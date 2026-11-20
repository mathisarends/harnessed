# Harnessed

[![PyPI version](https://img.shields.io/pypi/v/harnessed?style=for-the-badge&logo=pypi&logoColor=white&color=3776AB)](https://pypi.org/project/harnessed/)
[![Python versions](https://img.shields.io/pypi/pyversions/harnessed?style=for-the-badge&logo=python&logoColor=white&color=3776AB)](https://pypi.org/project/harnessed/)
[![CI status](https://img.shields.io/github/actions/workflow/status/mathisarends/harnessed/tests.yaml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=CI)](https://github.com/mathisarends/harnessed/actions/workflows/tests.yaml)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge)](https://github.com/mathisarends/harnessed/blob/main/LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-f59e0b?style=for-the-badge)](https://github.com/mathisarends/harnessed/blob/main/docs/releases.md)

A composable Python agent SDK in which everything is a plugin.

Inspired by [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness),
the agent itself is a small, fixed loop around a model. Every capability is a
plugin you list explicitly: files, shell, skills, MCP servers, subagents, output
spill and your own tools. The built-in plugins use the same public API as yours,
so you can read, replace or leave out any of them. A plugin keeps its tools,
instructions and event hooks together and reaches other plugins through typed
services, on a dependency-injection kernel inspired by
[Cordis](https://github.com/cordiverse/cordis). Harnessed owns the loop and every
plugin's lifetime; it adds no hidden default plugins.

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

`Agent` is reusable configuration: a fixed runtime core plus exactly the plugins
you list. Configure the loop with `loop=AgentLoop(max_steps=...)`. Tools run by default.
To restrict the agent to the calculator, pass
`approvals=Approvals(allow=[Calculator.add])`. An empty allowlist denies every tool.
`agent.run()` opens a session for one turn. Use `agent.session()`
when several turns should share a conversation.

Select a model with a `provider:model` string, such as
`Agent(model="openai-responses:gpt-6.1-terra")` (`OPENAI_API_KEY`). Harnessed
configures the PydanticAI adapter automatically. Use an explicit model object
when you need generation defaults or native provider configuration; see
[model configuration](https://github.com/mathisarends/harnessed/blob/main/docs/guides/models.md#choose-a-model).

## Everything is a plugin

### Compose a coding agent from parts

A coding agent is a list of plugins. Each line adds one capability, and removing
the line removes its tools, instructions and hooks:

```python
from harnessed import Agent, Approvals
from harnessed.plugins import (
    Filesystem,
    FilesystemAction,
    LocalWorkspace,
    Shell,
    ShellAction,
    Skills,
    Spill,
    Subagents,
)
from harnessed.plugins.mcp import MCP

reviewer = Agent(model="deepseek:deepseek-chat", instructions="Review the change.")

coder = Agent(
    model="deepseek:deepseek-chat",
    instructions="You are a careful coding agent.",
    plugins=[
        LocalWorkspace(root="."),  # where files and commands live
        Filesystem(),  # read_file, write_file, edit, grep, ...
        Shell(),  # run_command
        Skills(".agents/skills"),  # SKILL.md folders, loaded on demand
        Spill(),  # store large tool output, show the model a preview
        MCP.http("docs", url="http://localhost:8000/mcp"),
        Subagents({"review": reviewer}),  # delegate to an isolated child agent
    ],
    approvals=Approvals(
        ask=[
            FilesystemAction.WRITE,
            FilesystemAction.EDIT,
            FilesystemAction.DELETE,
            ShellAction.EXECUTE,
        ]
    ),
)

print(coder.describe())  # model, loop, every plugin, its slot and what it needs
```

### Swap the workspace, keep the tools

`Filesystem` and `Shell` do not know where they run. They ask for a
`FileSystemBackend` and a `ShellBackend`, and the workspace plugin provides both.
Replace the local workspace with a container and nothing else changes:

```python
from pathlib import Path

from harnessed.integrations.docker import DockerSandbox
from harnessed.integrations.sandbox import Workspace

sandbox = DockerSandbox(image="python:3.12-slim", workspace=Workspace.copy_from(Path(".")))
agent = Agent(model="deepseek:deepseek-chat", plugins=[sandbox, Filesystem(), Shell()])
```

Both workspaces claim the `workspace` slot, so listing two raises `PluginConflict`
when the agent is built instead of silently picking one.

### Write a plugin with the same seams

Your plugins get the same tools as the built-in ones: `needs()` injects a service
from another plugin, `Inject[ToolContext]` gives a tool its progress channel
without exposing it to the model, and a hook wraps every tool result like
middleware:

```python
from pathlib import Path

from harnessed import Plugin, ToolContext, tool
from harnessed.hooks import Next, ReviewToolResult
from harnessed.kernel import Inject, hook, needs
from harnessed.models import ToolResult
from harnessed.plugins.filesystem import FileSystemBackend


class LineCounter(Plugin):
    files = needs(FileSystemBackend)  # LocalWorkspace or a sandbox provides it

    @tool(readonly=True)
    async def count_lines(self, path: str, call: Inject[ToolContext]) -> int:
        """Count the lines in a workspace file."""
        await call.progress(f"Reading {path}")
        return len((await self.files.read(Path(path))).splitlines())

    @hook(ReviewToolResult)
    async def explain_errors(self, event: ReviewToolResult, next_: Next[ToolResult]) -> ToolResult:
        result = await next_()
        if result.is_error:
            return result.with_note("Check the path with list_dir before retrying.")
        return result
```

The model sees `count_lines(path: str)`. The plugin starts when its backend is
available, follows a replaced backend, and releases everything it registered when
the session closes.

### Test without a model

`ScriptedModel` replays a conversation, and `call()` scripts a tool call from the
decorated method and validates its arguments against the tool schema:

```python
from harnessed.testing import ScriptedModel, call


async def test_calculator() -> None:
    model = ScriptedModel([call(Calculator.add, a=19, b=23), "The result is 42."])
    result = await Agent(model=model, plugins=[Calculator()]).run("What is 19 + 23?")

    assert result.tool_calls[0][1].content == "42"
    assert result.text == "The result is 42."
```

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
| Compose plugins, inject services or mount temporary tools | [Plugins](https://github.com/mathisarends/harnessed/blob/main/docs/guides/plugins.md) |
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

Released under the [MIT License](https://github.com/mathisarends/harnessed/blob/main/LICENSE).
