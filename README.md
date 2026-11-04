# Harnessed

[![PyPI version](https://img.shields.io/pypi/v/harnessed?style=for-the-badge&logo=pypi&logoColor=white&color=3776AB)](https://pypi.org/project/harnessed/)
[![Python versions](https://img.shields.io/pypi/pyversions/harnessed?style=for-the-badge&logo=python&logoColor=white&color=3776AB)](https://pypi.org/project/harnessed/)
[![CI status](https://img.shields.io/github/actions/workflow/status/mathisarends/harnessed/tests.yml?branch=main&style=for-the-badge&logo=githubactions&logoColor=white&label=CI)](https://github.com/mathisarends/harnessed/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge)](https://github.com/mathisarends/harnessed/blob/main/LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-f59e0b?style=for-the-badge)](https://github.com/mathisarends/harnessed/blob/main/docs/releases.md)

A composable Python agent SDK. Keep tools, instructions and event hooks together
in plugins, combine them into reusable kits, and stream typed events from a live
session. Harnessed owns the agent loop and plugin lifetimes.

Python **3.12-3.14**, Pydantic v2. Optional provider integrations use PydanticAI's
Direct API.

Harnessed 0.x is **alpha software**. APIs and persisted session formats may
change in minor releases before 1.0. See the
[release notes and compatibility policy](https://github.com/mathisarends/harnessed/blob/main/docs/releases.md).
For opt-in persistence, atomic session commits and trace content filtering in
0.11.0, follow the
[upgrade from 0.10.0](https://github.com/mathisarends/harnessed/blob/main/docs/releases.md#upgrade-from-0100).
The earlier [0.10.0 prompt migration](https://github.com/mathisarends/harnessed/blob/main/docs/releases.md#upgrade-from-090)
remains available for applications upgrading from 0.9.0.

## Build an agent

```bash
uv add "harnessed[openai]"
```

```python
import asyncio

from harnessed import Agent, Instructions, Plugin, PluginOrder, SystemPrompt, tool
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
    prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
)


async def main() -> None:
    result = await agent.run("What is 19 + 23?")
    print(result.text)


asyncio.run(main())
```

One plugin colocates the tool, its model-facing instructions and its event hook.
`@tool` derives the input schema from Python types; the method docstring describes
the tool. `@hook` observes the selected event type across the session.
`SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED)` explicitly collects
complete instruction blocks in resolved plugin-list order.

`Agent` is reusable configuration. `agent.run()` opens a session for one turn;
use `agent.session()` when several turns should share a conversation.

Select a model with a `provider:model` string, such as
`Agent(model="openai-responses:gpt-6.1-terra")` (`OPENAI_API_KEY`). Harnessed
configures the PydanticAI adapter automatically. Use an explicit model object
when you need generation defaults or native provider configuration; see
[model configuration](docs/guides/models.md#choose-a-model).

## Stream typed events

Using the agent above, handle the events your application needs with ordinary
Python class patterns:

```python
from harnessed.events import (
    TextDeltaEvent,
    ToolCallFinishedEvent,
    ToolCallRequestedEvent,
    ToolProgressEvent,
    TurnFinishedEvent,
)


async def conversation() -> None:
    async with agent.session() as session:
        async for event in session.stream("What is 19 + 23?"):
            match event:
                case TextDeltaEvent(text=text):
                    print(text, end="", flush=True)
                case ToolCallRequestedEvent(call=call):
                    print(f"\nCalling {call.name}")
                case ToolProgressEvent(text=text):
                    print(f"\n{text}")
                case ToolCallFinishedEvent(call=call, result=result):
                    print(f"\n{call.name}: {result.status}")
                case TurnFinishedEvent(result=result):
                    print(f"\nFinished: {result.stop_reason}")

        follow_up = await session.run("Add 8 to that result.")
        print(follow_up.text)
```

Events are immutable typed values with stable tags such as `text.delta` and
`tool.call.finished`. The stream includes:

| Activity | Events |
| --- | --- |
| Generated text and reasoning | `TextDeltaEvent`, `ReasoningDeltaEvent` |
| Tool call previews | `ToolCallGenerationStartedEvent`, `ToolArgumentsDeltaEvent` |
| Tool calls and progress | `ToolCallRequestedEvent`, `ToolProgressEvent`, `ToolCallFinishedEvent` |
| Approval questions | `ApprovalNeededEvent` |
| Model requests and retries | `ModelRequestedEvent`, `ModelRequestFailedEvent`, `ModelRetryScheduledEvent` |
| Context compaction | `CompactionStartedEvent`, `CompactionAppliedEvent`, `CompactionFinishedEvent` |
| Large tool-output artifacts | `SpillCreatedEvent`, `SpillExpiredEvent` |
| Session status and turn result | `SessionStatusChangedEvent`, `TurnFinishedEvent` |

`ToolCallFinishedEvent` carries the final tool result, including denied or
interrupted calls. `TurnFinishedEvent` carries the assembled run result on normal
stream completion. Plugins can also hook audit events such as `StepEndedEvent`
and `TurnEndedEvent`, or declare their own event types.
See the [event reference](https://github.com/mathisarends/harnessed/blob/main/docs/reference/events.md) for all events and delivery surfaces,
and [streaming](https://github.com/mathisarends/harnessed/blob/main/docs/guides/streaming.md) for approval handling and stream cleanup.

## Compose capabilities with Kit

Bundle library defaults, nest kits, and replace individual policies at the
application boundary:

```python
from harnessed import Agent, Kit, PluginOrder, SystemPrompt
from harnessed.plugins import AgentLoop, Compaction, Filesystem, Shell


def workspace_kit(root: str) -> Kit:
    return Kit(
        "workspace",
        [
            Filesystem(root),
            Shell(root),
            AgentLoop(max_steps=30),
            Compaction(keep_steps=4),
        ],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )


workspace = workspace_kit(".").without(Shell)
agent = Agent(
    model="deepseek:deepseek-chat",
    plugins=[workspace, AgentLoop(max_steps=10)],
    instructions="Help the user understand this codebase. Cite file paths in your answer.",
)

print(agent.describe())
```

`without(Shell)` removes that capability from the kit. The direct `AgentLoop`
replaces the kit's loop through its `loop` slot; custom plugins can declare slots
with `class MyPolicy(Plugin, slot="my.policy")`. Unslotted plugins are additive.
`SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED)` follows the selected
plugins, and `agent.describe()` shows their origins, replacements and exclusions before any session starts.

Tools, approvals, output limits, compaction and tracing all compose through
plugins. See [plugin composition](https://github.com/mathisarends/harnessed/blob/main/docs/guides/plugins.md) for nested-kit precedence and lifecycle rules.

## Inject services into tools

Separate a backend from the capabilities that use it. `Service` publishes an
implementation; `Inject[T]` supplies it without exposing it as a model argument:

```python
from pathlib import PurePath

from harnessed import Agent, Plugin, ToolContext, tool
from harnessed.kernel import Inject
from harnessed.plugins import Service
from harnessed.plugins.filesystem import FileSystemBackend, LocalFileSystem


class ProjectDocs(Plugin, needs=(FileSystemBackend,)):
    @tool(readonly=True)
    async def read_document(
        self, path: str, *, files: Inject[FileSystemBackend], ctx: ToolContext
    ) -> str:
        """Read up to 4,000 characters from a workspace document."""
        await ctx.progress(f"Reading {path}")
        return await files.read(PurePath(path), limit=4_000)


agent = Agent(
    model="deepseek:deepseek-chat",
    plugins=[Service(FileSystemBackend, LocalFileSystem(".")), ProjectDocs()],
)
```

The model supplies only `path`. `ToolContext` provides per-call progress and
cancellation; `needs=(FileSystemBackend,)` ties the plugin's lifetime to that
service. Removing the provider suspends its dependent tools; restoring it creates
a fresh activation. Backends implement ordinary Python protocols, so the same
tools can use a local, remote or sandboxed implementation.

## Add capabilities for part of a session

Using `Calculator` from the first example, mount its tools, instructions and hooks
for a bounded scope:

```python
from harnessed import Agent, SectionDelivery


async def temporary_tools() -> None:
    agent = Agent(model="deepseek:deepseek-chat")
    async with agent.session() as session:
        async with await session.use(Calculator(), section_delivery=SectionDelivery.CONTEXT):
            result = await session.run("What is 19 + 23?")
            print(result.text)
```

Leaving the inner scope removes the tool, hook and mount-owned guidance.
`SectionDelivery.CONTEXT` delivers late instructions as active context; the
session's system prompt stays fixed. Each activation has its own lifetime and
can keep mutable data in `PluginState`.

Sessions also expose named context, steering, pause/resume and immutable
`SessionState` snapshots. Opt into automatic persistence with
`Agent(store=JsonSessionStore(root=...))`, importing the JSON store from
`harnessed.integrations.json`, or implement `SessionStore` in your conversation
repository. Stored conversations reopen with `agent.session(id=...)`; the default
remains in memory. See
[sessions](https://github.com/mathisarends/harnessed/blob/main/docs/guides/sessions.md) and
[context](https://github.com/mathisarends/harnessed/blob/main/docs/guides/context.md) for those controls.

## Read by topic

Start with the [documentation index](https://github.com/mathisarends/harnessed/blob/main/docs/index.md) or the
[SDK reference](https://github.com/mathisarends/harnessed/blob/main/docs/reference/sdk.md).

- [Models and provider settings](https://github.com/mathisarends/harnessed/blob/main/docs/guides/models.md)
- [Tools, hooks and cancellation](https://github.com/mathisarends/harnessed/blob/main/docs/guides/tools.md)
- [Plugin composition and lifecycle](https://github.com/mathisarends/harnessed/blob/main/docs/guides/plugins.md)
- [MCP server tools over stdio and HTTP](https://github.com/mathisarends/harnessed/blob/main/docs/guides/mcp.md)
- [Docker and Bubblewrap sandboxes](https://github.com/mathisarends/harnessed/blob/main/docs/guides/sandboxes.md)
- [Instructions and changing context](https://github.com/mathisarends/harnessed/blob/main/docs/guides/context.md)
- [Sessions, controls and application-owned state](https://github.com/mathisarends/harnessed/blob/main/docs/guides/sessions.md)
- [Typed events and custom extensions](https://github.com/mathisarends/harnessed/blob/main/docs/guides/events.md)
- [Approvals and headless operation](https://github.com/mathisarends/harnessed/blob/main/docs/guides/approvals.md)
- [Offline testing](https://github.com/mathisarends/harnessed/blob/main/docs/guides/testing.md)
- [OpenTelemetry tracing with Phoenix and Langfuse](https://github.com/mathisarends/harnessed/blob/main/docs/guides/tracing.md)
- [Executable examples](https://github.com/mathisarends/harnessed/blob/main/docs/guides/examples.md)
- [Development and CI](https://github.com/mathisarends/harnessed/blob/main/docs/contributing/development.md)
- [Changelog and breaking changes](https://github.com/mathisarends/harnessed/blob/main/Changelog.md)

The standalone `harnessed.kernel` uses only the standard library. Optional
features live under `harnessed.plugins`; providers live under `harnessed.models`.

Inspired by [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
and [Cordis](https://github.com/cordiverse/cordis).

Released under the [MIT License](https://github.com/mathisarends/harnessed/blob/main/LICENSE).
