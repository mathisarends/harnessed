# System prompts and changing context

Use `instructions=` for fixed rules, ordinary user messages for tasks, and
`PrepareRequest` hooks for information needed by the next model request. Append
a message to the session only when it belongs in conversation history.
`observe()` records time-bound text/image snapshots with their source and refs.

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

## Plugin instructions are collected by default

An agent appends every selected plugin's instruction block after its own
`instructions=` text. No prompt configuration is needed:

```python
from harnessed import Agent

agent = Agent(
    instructions="You are a browser agent.",
    plugins=[Observe(config=ObserveConfig())],
)
print(agent.prompt)  # lists every section and the plugin it came from
```

Use `SystemPrompt.from_plugins(...)` to keep collection while adding fixed
content or choosing a format:

```python
from harnessed import Agent, PromptFormat, SystemPrompt

agent = Agent(
    instructions="You are a browser agent.",
    plugins=[Observe(config=ObserveConfig())],
    prompt=SystemPrompt.from_plugins(format=PromptFormat.MARKDOWN),
)
```

Collection keeps complete instruction blocks in final configured
plugin-list order. Nested packs expand at their list position. Removed and
replaced recipes are discarded before providers run. A slot replacement retains
the inherited position. Dependency setup order never changes prompt order.
Configuration providers run once when constructing the agent, after plugin
selection; sessions reuse that content.

To own the complete system prompt, pass a document built with the constructor,
such as `prompt=SystemPrompt("...")`. Plugin instruction blocks are then left out
without calling their providers, and `print(agent.prompt)` lists the omitted
plugins so the choice stays visible.

`Agent(instructions="...")` prepends application-owned text. `prompt=` accepts a
`SystemPrompt` document or collection strategy. Fixed application content in
`SystemPrompt.from_plugins(*parts)` follows the caller text and precedes
plugin blocks. No plugin is moved to the end by its type or name: put completion
and policy components at their intended list positions.

## Publish the strategy in a pack

```python
from harnessed import Agent, PluginPack, PromptFormat, SystemPrompt
from harnessed.plugins import Filesystem, Shell

workspace = PluginPack(
    "workspace",
    plugins=[Filesystem(), Shell()],
    prompt=SystemPrompt.from_plugins(
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
session's kernel `Scope`. Registrations and cleanup belong to that session.
Return these values inside the plugin's `SystemPrompt`, or include application
values in the outer document. Configuration methods run once per agent; these
session-dependent content sources run once per opened session.

`print(agent.prompt)` lists the format, final section order, plugin origins and
previews. Files and factories show paths or descriptions. Inside a session,
`await session.prompt.render()` returns the actual fixed text. The system prompt
is an immutable session snapshot, including while a plugin waits for dependencies
or is suspended. `session.messages` exposes retained conversation input.
Request-only contributions are visible in recorded model requests, not in that
conversation view.

## Runtime section delivery

`session.use(plugin)` defaults to `SectionDelivery.ERROR`: a runtime plugin with
declared system sections is rejected before setup or event registration. Choose
delivery explicitly when mounting such a capability:

```python
from harnessed import SectionDelivery

handle = await session.mount(interaction, section_delivery=SectionDelivery.CONTEXT)
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
the whole agent. Use `PrepareRequest` for changing runtime facts.


## Tasks are user messages

`run()`, `submit()` and `stream()` accept text or a `UserMessage`. Text is the
normal form; it becomes a user message. Include task background in that input:

```python
result = await agent.run("Review the login fix.\n\nTicket: Login currently returns HTTP 500.")
```

There is no `context=` keyword or automatic dictionary-to-prompt formatting.
Explicit `UserMessage` values support text/image parts and preserve their identity.
Files and images can also be supplied through `attachments=` as before.

## Enrich one model request

```python
from harnessed import Plugin
from harnessed.hooks import Next, PrepareRequest
from harnessed.kernel import hook
from harnessed.models import ModelRequest, UserMessage


class ProjectKnowledge(Plugin):
    @hook(PrepareRequest)
    async def enrich(self, event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
        request = await next_()
        return request.append_message(UserMessage("Project knowledge: Run tests with pytest."))
```

`ModelRequest.append_message(message)` returns a new request. It retains the
original request and all its other fields, including tools, settings and the
output contract. It adds no labels or markup. The new message belongs to this
request; it does not enter session history or a conversation snapshot.

`await next_()` runs downstream hooks and returns their result. Appending after
that call composes contributions as the chain returns, from the inner hook to
the outer hook. Use the kernel's explicit priority rules when ordering matters.
`PrepareRequest` is the single request transformation hook; `ConfigureRequest`
has been removed. The SDK applies output constraints, compaction and token checks
after preparation and records the final request before calling the model.

Use `event.session` to inspect the restricted plugin view. Services still belong
in dependency injection, rather than model input. A plugin can keep its own
runtime state when computing a contribution. Removing or suspending the plugin
also removes its request hook, so future requests stop receiving its contribution.
`ContextValue`, the public `DynamicContext` service and `context_description`
have been removed; no named context registry or implicit XML rendering is needed.

## Append a message without running the agent

```python
from harnessed.models import UserMessage

async with agent.session() as session:
    await session.append_message(UserMessage("The deployment failed at 14:20."))
    result = await session.run("Investigate the failure.")
```

`await session.append_message(message: UserMessage)` is a durable, additive
history write. It does not start work or wake an idle session. Every call adds a
message, including identical content. A configured store commits the message
before the method returns. Await the write before work that must see it.
The same method is available on the restricted `SessionContext` in hooks.

Subsequent requests see the message until ordinary history compaction replaces
or removes it. It survives state export and stored-session reopening. It does
not change the system prompt or an already assembled request. The old
`add_context()` name is removed without an alias.

## Steering, observations and retrieval

`await session.steer("Focus on authentication first.")` adds input to the next
step of an active turn, or starts a turn while idle. It is an instruction to do
work, rather than a quiet history write. Concurrent runs queue separate turns.

For automatically captured browser/workspace state, a `PreStep` hook supplies an
ordinary `UserMessage` through `event.session.observe()`:

```python
from harnessed.models import ImagePart, TextPart, UserMessage

# Inside a PreStep hook, after capturing the browser:
event.session.observe(
    UserMessage((TextPart(browser_text), ImagePart(screenshot_url))),
    source="browser",
    refs=(snapshot_id,),
)
```

`observe(message: UserMessage, *, source: str, refs: Iterable[str] = ())` buffers
the latest pending snapshot for each source. Text, image-only content and mixed
content use the same message type. Source and refs stay outside the message;
they are SDK provenance, not provider fields, attachment locations or generated
prompt markup. The separate `Observation` input container has been removed.

The next step records the message with its source, refs, turn and step. Its
message ID is preserved. Inline images are stored as asset references before
recording. Once recorded, text and images stay together in their history position;
later observations do not rewrite them. Unrecorded observations are not exported.
See [image assets](sessions.md#image-assets-and-memory).

Ordinary compaction removes old snapshots with their steps. Installing
`LatestObservationOnly()` explicitly retains only the newest recorded snapshot
per source. This can change the request prefix; retaining prior observations
lets later steps reuse an unchanged prefix. Neither policy guarantees provider
cache hits. Durable audit records retain the original observations.

After request hooks and compaction, `ObservationsIncludedEvent` identifies the
complete observations included in a `ModelRequestedEvent` with the same
`request_id`. Each receipt contains `source`, `refs`, `turn_id`, `step` and
`message_id`. Use it to acknowledge hints or correlate coordinate actions with
the screenshot delivered in that request.

Removing or changing a message, including dropping one of its images, removes
its original receipt. Matching text alone is not proof of inclusion. A receipt
records the final assembled request, not provider acceptance or model perception.

Large optional knowledge bases fit retrieval tools: injected services perform
the search when the model requests it, instead of adding all knowledge to every
request. Advice about one tool result can use `result.with_note(...)`.
