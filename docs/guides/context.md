# System prompts and changing context

Use system instructions for persistent rules, `session.add_context(...)` for
additional background, `ContextValue` plugins for changing named facts, steering
for course corrections, and tools for knowledge retrieved on demand.

## Declare one instruction block per plugin

A configured plugin supplies its content through one synchronous method:

```python
from harnessed import Instructions, Plugin, PluginConfig, SystemPrompt


class ObserveConfig(PluginConfig):
    screenshots: bool = True


class Observe(Plugin[ObserveConfig], slot="browser.observe"):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt(
            Instructions(title="Page state", text="Use the latest observation."),
            "Use the screenshot for visual details." if self.config.screenshots else None,
        )
```

The base method returns an empty document. Override it only when the capability
provides instructions. Static text can live in module constants; configuration
helpers can return content too. The method returns actual content, never section
references. It must be pure and use only recipe configuration, without injected
services or live activation state. A class docstring remains Python documentation.

## Select plugin order explicitly

The application opts into collection with a required order argument:

```python
from harnessed import Agent, PluginOrder, PromptFormat, SystemPrompt

agent = Agent(
    instructions="You are a browser agent.",
    plugins=[Observe(config=ObserveConfig())],
    prompt=SystemPrompt.from_plugins(
        order=PluginOrder.AS_LISTED,
        format=PromptFormat.MARKDOWN,
    ),
)
```

`PluginOrder.AS_LISTED` collects complete instruction blocks in final configured
plugin-list order. Nested packs expand at their list position. Removed and
replaced recipes are discarded before providers run. A slot replacement retains
the inherited position. Dependency setup order never changes prompt order.
Configuration providers run once when constructing the agent, after plugin
selection; sessions reuse that content.

A selected plugin overriding `instructions()` requires this explicit strategy.
Omitting it raises `ConfigError` before providers are called, even when an
override would return empty content. There is no default ordering argument.
Plain plugins inheriting the empty base method require no prompt configuration.

`Agent(instructions="...")` prepends application-owned text. `prompt=` accepts a
`SystemPrompt` document or collection strategy. Fixed application content in
`SystemPrompt.from_plugins(*parts, order=...)` follows the caller text and precedes
plugin blocks. No plugin is moved to the end by its type or name: put completion
and policy components at their intended list positions.

## Publish the strategy in a pack

```python
from harnessed import Agent, PluginPack, PluginOrder, PromptFormat, SystemPrompt
from harnessed.plugins import Filesystem, Shell

workspace = PluginPack(
    "workspace",
    plugins=[Filesystem(), Shell()],
    prompt=SystemPrompt.from_plugins(
        order=PluginOrder.AS_LISTED,
        format=PromptFormat.MARKDOWN,
    ),
)
agent = Agent(
    instructions="Help with the workspace.",
    plugins=[workspace.without(Shell)],
)
```

The pack declares the strategy; the agent resolves it against the complete final
plugin composition, including external plugins. `None` entries are skipped in
both `Agent(plugins=...)` and `PluginPack(plugins=...)`. `without()` returns a new pack and
preserves its prompt strategy.

PluginPack prompts participate in the existing `instructions` slot: a parent pack's
prompt overrides child defaults, and explicit `Agent(prompt=...)` overrides pack
defaults. Competing sibling defaults require an explicit decision. Supplying
`Agent(instructions=...)` adds caller text without replacing the selected strategy.
See [pack composition](plugins.md#publish-library-defaults-with-pluginpack).

## Compose content and select a renderer

`SystemPrompt(*parts, format=PromptFormat.PLAIN)` accepts strings, `Instructions`
and nested documents. `None` is removed during construction. Other unsupported
values raise `TypeError`. Empty rendered content is omitted. Equal or repeated
content is retained; content values have no plugin-reference identity.

```python
from harnessed import Agent, Instructions, PromptFormat, SystemPrompt

agent = Agent(
    instructions="Be concise.",
    prompt=SystemPrompt(
        Instructions(title="Workspace", text="Keep changes focused."),
        SystemPrompt("Report the result."),
        format=PromptFormat.MARKDOWN,
    ),
)
```

| Format | Rendering |
| --- | --- |
| `PromptFormat.PLAIN` | Text separated by blank lines; titles are diagnostic metadata |
| `PromptFormat.MARKDOWN` | Optional `## Title` headings followed by content |
| `PromptFormat.XML` | Escaped `<section title="Title">...</section>` elements |

The outermost format controls nested content and all collected plugin blocks.
`from_plugins` belongs only to the outermost document. Plugin methods return
content documents, not another collection strategy. There are no fragments,
placement selectors, exclusions or unplaced policies. Override a plugin's
`instructions()` method to replace its whole block or return an empty document.

## Files, session factories and inspection

`Instructions.from_file(path="AGENTS.md", missing_ok=True)` reads a file once per
opened session. `Instructions.from_factory(factory=callback, title="Catalog",
description="Read at session startup")` accepts an async callback taking the
session's kernel `Context`. Registrations and cleanup belong to that session.
Return these values inside the plugin's `SystemPrompt`, or include application
values in the outer document. Configuration methods run once per agent; these
session-dependent content sources run once per opened session.

`print(agent.prompt)` lists the format, final section order, plugin origins and
previews. Files and factories show paths or descriptions. Inside a session,
`await session.prompt.render()` returns the actual fixed text. The system prompt
is an immutable session snapshot, including while a plugin waits for dependencies
or is suspended. `session.messages` exposes retained conversation input;
`session.snapshot().context` contains current durable plugin-owned named facts.

## Runtime section delivery

`session.use(plugin)` defaults to `SectionDelivery.ERROR`: a runtime plugin with
declared system sections is rejected before setup or event registration. Choose
delivery explicitly when mounting such a capability:

```python
from harnessed import SectionDelivery

handle = await session.use(interaction, section_delivery=SectionDelivery.CONTEXT)
await handle.aclose()
```

`CONTEXT` evaluates configuration providers and session factories once per mount
activation and owns their guidance on that mount. The guidance is immediately
available to subsequent model requests, and
disappears from active context on unmount or dependency suspension. A restart
evaluates it again. Setup, provider, file or factory failures roll back the mount's
registrations and resources. Factory effects belong to the mount too.

`SectionDelivery.IGNORE` mounts tools without evaluating section providers,
reading section files or running section factories. Runtime guidance does not
become a durable named fact and is not restored when reopening a session. Actual
request records still retain the delivered content for inspection.

The system text remains stable; new tools still change the tool catalog. This
alone does not guarantee provider prompt-cache reuse.

Keep guidance about one tool's parameters, return values and normal use in that
tool's description. Use sections for rules spanning multiple tools, workflows or
the whole agent. Use `ContextValue` for changing runtime facts.


## Add context without running the agent

```python
async with agent.session() as session:
    await session.add_context("Include source URLs.")
    await session.add_context("The project uses Python 3.12+.")
    result = await session.run("Review the project.")
```

`add_context(content: str) -> None` appends a user-role text contribution to
conversation history. It does not start a turn, wake an idle session or invoke
the model. Await it before starting work that must see the contribution. During
active work, requests assembled after the write see it; an already assembled or
in-flight request is unchanged. A `PreStep` hook can await `add_context` to include
background in that step's request.

Every call appends, including identical text. There are no names, replacement or
removal operations. Contributions remain available across turns and through
`SessionState` or stored-session restoration, subject to ordinary history
compaction. Older contributions may be summarized rather than retained verbatim.
They do not change the fixed system prompt. Inspect retained contributions through
`session.messages` or `session.snapshot().history`. A configured session store
commits the contribution before the method returns; without a store it remains
in memory. Calls after closure raise `SessionClosed`.

The former `session.context.set(name, value)` and `session.context.remove(name)`
API has been removed. Replace a quiet contribution with `add_context(value)`;
this changes keyed updates into additive history. Put fixed rules in
`Agent(instructions=...)`, or use a `ContextValue` plugin for changing named facts.

## Facts and automatic context

```python
from harnessed.plugins import ContextValue

# Supply this plugin when constructing the agent:
current_branch = ContextValue("branch", lambda: read_current_branch())
```

A ContextValue refreshes before each step and appends only actual changes.
Disposal removes the value it owns. For a one-task contribution, use
`await session.run("Review", context={"ticket": ticket_text})`; attachments can
travel with that run using `File(...)` or `Image(...)`.

## Steering, observations and retrieval

`await session.steer("Focus on authentication first.")` goes into the current
turn before its next step. While idle, it starts a new turn without waiting for
its result. Concurrent `run(...)` calls queue separate turns.

An `Observation` is an immutable state snapshot supplied by the application. A
plugin can capture it automatically in `PreStep`, before every model invocation;
the model does not need to call an observation tool. The existing
`session.observe()` method buffers the latest pending snapshot per source and the
runtime records it with the current turn and step.

```python
from harnessed import Observation
from harnessed.models import ImagePart, TextPart

# Inside a PreStep hook, after capturing the browser state:
event.session.observe(
    Observation(
        source="browser",
        content=(TextPart(browser_text), ImagePart(screenshot_url)),
        refs=(snapshot_id,),
    )
)
```

`content` accepts a string or a tuple of text, `ImagePart` and `ImageReference`
parts, including an
image-only tuple. Text and images are projected together as one `UserMessage`.
The user role is the model's input role; it does not mean a human authored the
snapshot. `refs` identify application state and are not attachment locations.

Inline image data is stored as an `ImageReference` before recording; see
[image assets](sessions.md#image-assets-and-memory). Recorded snapshots then keep
the same content, identity and history position after
their step ends. They are not automatically replaced by summaries. Ordinary
steps append to the existing request prefix, which allows provider caches and
the JSONL prefix codec to reuse unchanged history. This does not guarantee a
particular provider cache hit rate.

Compaction removes old snapshots with their steps, including their images, in
the request that applies the summary. `Compaction(keep_steps=5, ...)` retains five
completed steps in the active turn plus the current step; this is retention at
compaction boundaries, not a window that removes a snapshot after every step.
To explicitly replace older state instead, install `LatestObservationOnly()`
from `harnessed.plugins`. It retains one unchanged snapshot per source and can
change the request prefix whenever a replacement arrives. Original snapshots
remain available in the durable log under either policy.

Large knowledge bases fit tool retrieval: injected services perform the search,
and the model requests the tool when needed. Retrieval avoids paying for unused
knowledge on every step.

Attach application identifiers with `Observation(..., refs=("hint-1", "hint-2"))`.
These refs are durable metadata, separate from the rendered text. After all
request hooks and compaction, `ObservationsIncludedEvent` follows
`ModelRequestedEvent` with the same `request_id`. Each `IncludedObservation`
contains `source`, `refs`, `turn_id`, `step` and `message_id`.

Use this event to acknowledge included hints or select the screenshot used by
coordinate tools. Match the relevant source and refs, and use the explicit turn,
step and message identity instead of parsing message IDs. An empty inclusion
tuple means no observation message survived in that request. Transformations
must retain the message's identity and complete content to retain its provenance:
removing an image, redacting text or dropping a snapshot removes its receipt, as
does compaction. Text matching an application ID elsewhere in the prompt never
counts as inclusion. A receipt describes the final submitted request, not proof
that a provider accepted it or that the model perceived a particular detail.

| Information | Mechanism |
| --- | --- |
| Role, rules, permanent guidance | SystemPrompt / Instructions |
| Changing project facts | Named context / ContextValue |
| Additive conversation input without starting a turn | await session.add_context(...) |
| Task metadata and attachments | run(context=..., attachments=...) |
| Course correction | steer(...) |
| Current browser or workspace state | Observation |
| Large optional knowledge | A retrieval tool |
| Advice about one tool result | result.with_note(...) |

For long conversations, [compaction](compaction.md) keeps selected turns verbatim
and replaces older working context with its current summary and retained messages.
