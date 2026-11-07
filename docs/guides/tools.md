# Tools and hooks

Declare public tools as `@tool` methods on a `Plugin`. Method names, explicit
descriptions and annotations supply their names, model instructions and strict
argument schemas. A docstring is the fallback when no description is supplied.
Python tools use this declaration path, including tools mounted at runtime:

```python
from harnessed import Plugin, tool
from harnessed.kernel import Inject
from my_application import Database


class Lookups(Plugin):
    needs = (Database,)

    @tool(readonly=True, description="Find one record by its exact key.")
    async def lookup(self, key: str, *, db: Inject[Database]) -> str:
        """Resolve the injected backend and return its serialized record."""
        return await db.lookup(key)


# plugins=[Lookups()] initially, or await session.mount(Lookups()) at runtime.
```

`description="..."` is sent to the model without replacing the Python docstring,
so developer documentation can describe implementation details independently.
`description=None` (the default) uses `inspect.getdoc` as a fallback; a missing
docstring produces an empty description. An explicit `description=""` suppresses
the description rather than falling back. Parameter descriptions stay in
`Annotated[..., Field(description="...")]` or `parameter_fields` metadata.

`needs` controls the plugin's lifetime. Removing Database disposes its tools;
restoring the service restarts the plugin. `Inject` resolves per-call services
and keeps those arguments out of the model's schema. Related tools, hooks,
configuration and cleanup belong to the same class.

`Scope`, `Bundle` and `Session` do not provide separate tool decorators or
function-list registration. Functional kernel plugins remain useful for services
and hooks. Kernel consumers use `harnessed.kernel.Scope` without importing
Pydantic or the agent runtime.

[MCP](mcp.md) is the schema-defined remote integration. It registers external
definitions through `ToolRegistry.register_external(definition, handler, *,
owner, spec=None)`. The typed asynchronous handler receives JSON arguments and
a `ToolContext`, validates its external schema and returns `ToolResult`. Core
owns registrations and uses the same availability, scheduling, approval and
execution pipeline as Python tools; it has no MCP or JSON Schema dependency.

## Tool display and integration metadata

The native decorator accepts display options inspired by FastMCP without requiring
the MCP extra. Existing `name`, `description`, `enabled` and `timeout` options keep
their normal execution behavior.

```python
from harnessed import Plugin, ToolAnnotations, ToolIcon, ToolPresentation, tool


class Documents(Plugin):
    @tool(
        presentation=ToolPresentation(
            title="Search documents",
            tags={"documents", "search"},
            icons=(ToolIcon("https://example.com/search.svg", mime_type="image/svg+xml"),),
            annotations=ToolAnnotations(idempotent_hint=True, open_world_hint=False),
            meta={"ui": {"category": "workspace"}},
        ),
        readonly=True,
    )
    async def search(self, query: str) -> str:
        """Find documents matching the query."""
        return f"Matches for {query}"
```

| ToolPresentation field | Meaning |
| --- | --- |
| `title: str \| None` | An optional human-readable display title. |
| `tags: Iterable[str]` | Nonempty labels for application grouping and filtering. |
| `icons: Iterable[ToolIcon]` | Icon sources, optional MIME types, sizes and light/dark themes. Harnessed does not fetch them. |
| `annotations: ToolAnnotations \| None` | Optional `read_only_hint`, `destructive_hint`, `idempotent_hint` and `open_world_hint` booleans. |
| `meta: JSONMapping \| None` | JSON metadata for application integrations. |

Hooks read the captured values through `event.tool.presentation: ToolPresentation`.
Tags become a `frozenset`, icons a tuple and JSON metadata is copied and deeply
frozen at declaration. A prepared call retains its original presentation across
registration replacement. These values stay process-local: they do not change
model tool definitions, argument schemas or persisted events. MCP tools populate
the same presentation from their remote catalog, including FastMCP tags.

Annotations describe behavior; they never grant permissions. Only `readonly=True`
and the application's approval policy control local authorization. Use `metadata=`
for typed Python application values, and `presentation.meta` for JSON integration data.
Synchronous tools continue to run in worker threads; async tools run on the event loop.

## Pydantic parameter objects

Use `@tool(params=SearchParams)` to receive all model-supplied arguments as one
validated Pydantic object. Its fields form the tool's top-level JSON schema:

```python
from pydantic import BaseModel, Field

from harnessed import Plugin, tool
from harnessed.kernel import Inject
from my_application import CatalogClient


class SearchParams(BaseModel):
    query: str = Field(description="Text to search for")
    limit: int = Field(default=5, ge=1, le=20)


class CatalogTools(Plugin):
    @tool(description="Search the catalog", params=SearchParams, readonly=True)
    async def search(self, params: SearchParams, *, client: Inject[CatalogClient]) -> str:
        return await client.search(params.query, limit=params.limit)
```

The model sends `{"query": "keyboard"}`; the method receives
`SearchParams(query="keyboard", limit=5)`. Field descriptions, constraints,
aliases, default factories and model validators come from `SearchParams`.
Validation is strict and rejects unknown inputs before invocation, including when
the parameter model's configuration would otherwise ignore or allow extra fields.
Other validation settings, including validation of field defaults, follow the model.

The method must declare exactly one non-injected parameter, annotated with the
same model class; its Python name is arbitrary and it may be keyword-only. Put
defaults on the model's fields rather than on that method parameter. Injected
services and a keyword-only `ToolContext` remain separate and hidden from the
schema. `params` requires a `BaseModel` subclass and does not accept `RootModel`.
It cannot be combined with `parameter_fields`; declare field metadata on the
parameter model instead.

Without `params=...`, each non-injected Python parameter still forms its own
schema property. For example, `search(self, params: SearchParams)` with plain
`@tool` expects `{"params": {"query": "keyboard"}}`.
Scripted calls use the same top-level arguments:
`call(CatalogTools.search, query="keyboard")`.

## Conditional tools

`@tool(enabled=False)` hides a tool from model requests and prevents its execution.
For conditions that change during a session, pass a synchronous predicate returning
a bool. An unbound instance method is bound to the same mounted plugin as the tool:

```python
from dataclasses import dataclass

from harnessed import Plugin, tool
from harnessed.kernel import Inject


@dataclass
class PointerState:
    has_snapshot: bool = False


class PointerTools(Plugin, needs=(PointerState,)):
    def can_click(self, *, state: Inject[PointerState]) -> bool:
        return state.has_snapshot

    @tool(enabled=can_click)
    async def click_at(
        self, x: float, y: float
    ) -> str: ...  # Dispatch through the application's browser backend.
```

Provide `PointerState` with `Service(PointerState, state)`. A plain function can
also serve as the predicate. Its parameters must use `Inject[T]`; required
predicate services missing from the current scope disable only that tool, and
optional services receive None. Injection resolves current services on every
check and never adds predicate parameters to the model's tool schema. `needs`
continues to control the whole plugin's lifetime independently.

Conditions are evaluated when building model requests, before policy/approval,
and again immediately before invoking the tool after approval and scheduling
waits. A disabled call returns `ToolResult.unavailable(...)`, including when a
model calls a tool omitted from its request. Disabling an overriding registration
does not expose an older tool with the same name. Prepared calls retain their
original binding and never follow replacement registrations.

Keep predicates fast and free of side effects: they may run multiple times per
step. They cannot depend on model arguments or per-call `ToolContext`, and async
predicates are rejected. Non-boolean results and predicate exceptions fail closed:
request construction raises, while an invocation returns a tool error. Refresh
expensive external state before the request, for example in `PreStep`. Browser
backends must still validate their own invariants if state changes during an
asynchronous action.

`PrepareRequest` can separately filter model-facing tools; that alone does not
prevent execution. `ToolPolicy` remains the authorization/approval mechanism.
See the [offline conditional tools example](https://github.com/mathisarends/harnessed/blob/main/examples/30_conditional_tools.py).

## Per-call state

```python
from harnessed import Plugin, ToolContext, tool


class Worker(Plugin):
    @tool
    def process(self, path: str, *, call: ToolContext) -> str:
        if call.cancelled:
            return "Stopped before processing."
        return process_file(path, cancellation=call.signal)
```

A keyword-only `ToolContext` is supplied automatically; `Inject[ToolContext]`
is also supported. Its signal, progress, call and live conversation state belong
to this call. It does not expose the model driver. Normal service arguments use
`Inject[T]`; an unmarked application parameter always belongs to the model.

## Ask for approval inside a tool

A tool that discovers a risky step mid-call asks with `await call.approve(reason)`.
The request is durable and runs through the session approval flow: an `ALWAYS`
grant for the tool and scope (default `"tool-call"`), the approver configured on
`Approvals(approver=...)`, or a stream consumer answering `events.ApprovalNeededEvent`.
It returns True for allow or always and raises `ApprovalUnavailable` when nobody
can answer.

```python
@tool
async def cleanup(self, *, call: ToolContext) -> str:
    if not await call.approve("Delete 3 generated files?"):
        return "Kept the files."
    ...
```

Free-text clarifying questions are not a tool API. Let the model end its turn with
the question; the user's answer arrives as the next run.

## Cancellation and parallel work

Each finished invocation writes and flushes a `tool.execution.finished` receipt
before waiting for ordered result review. The receipt contains unreviewed output in
the audit log; it is not a model-visible result. `ToolCallFinishedEvent`
(`tool.call.finished`) commits the reviewed and limited result in model order,
once for both history and the UI stream. There is no separate completion event.
If execution finished but review did not, recovery retains the receipt and returns
an explicit interrupted-review error without exposing raw output or repeating the
action. Audit consumers must treat execution receipts as unreviewed data.
Receipt text respects the tool's configured output limit, including truncation
metadata; review hooks and Spill still receive the complete execution result.
Execution and reviewed-result checkpoints drain their write and flush on cancellation,
even if another append holds the log lock. Event observers run after that checkpoint
and remain cancellable.

Async tools receive task cancellation on abort or timeout. Put resource cleanup
in `finally`. Synchronous tools run in a worker thread: Python cannot forcibly
stop a running thread. Long synchronous operations must poll `call.cancelled`
or pass `call.signal` to an operation that cooperates. Abort waits for the worker
to finish before releasing its resources or closing the session.

Shell commands use their requested timeout or `Shell(timeout=...)` default, up to
`max_timeout` (at most 600 seconds). The shell tool explicitly overrides the generic
`AgentLoop(tool_timeout=...)` deadline so longer commands can complete.
Its argument schema and runtime validation both advertise the configured `max_timeout`.

Background execution is a separate opt-in capability. Add `BackgroundShell(...)`
alongside a shell backend to expose start, live inspection, stdin and termination
tools with session-inbox completion notifications. See the
[background shell guide](background-shell.md) for registration and a runnable example.

For configuration-dependent constraints, `@tool(parameter_fields=fields)` accepts
an unbound method `fields(self) -> Mapping[str, FieldInfo]`. It runs once per mount
after `setup` and adds Pydantic `Field` metadata to the named model parameters.
For example, return `{"timeout": Field(le=self.config.max_timeout)}`. Annotations
still supply the types and defaults; injected or unknown parameter names are rejected.

Tools run sequentially unless `parallel=True`. Declare shared resource keys for
parallel tools that touch the same file or external object; overlapping keys
serialize those calls. Results commit in the model's original call order even
when execution finishes out of order. An explicit `override=True` is required to
replace an existing live tool name. Removing that registration restores the
previous owner.

See [the runnable class plugin example](https://github.com/mathisarends/harnessed/blob/main/examples/06_explicit_registration.py).

## Application metadata and captured tool descriptions

Attach immutable application values without changing the model's tool schema:

```python
from dataclasses import dataclass

from harnessed import Plugin, ToolResult, tool
from harnessed.hooks import Next, ReviewToolResult
from harnessed.kernel import hook


@dataclass(frozen=True)
class AuditTag:
    category: str


class Workspace(Plugin):
    @tool(readonly=True, metadata=(AuditTag(category="workspace"),))
    async def inspect(self) -> str:
        return "Workspace inspected."

    @hook(ReviewToolResult)
    async def review(self, event: ReviewToolResult, next_: Next[ToolResult]) -> ToolResult:
        tag = event.tool.metadata.get(AuditTag) if event.tool is not None else None
        result = await next_()
        return result.with_note(f"Audit category: {tag.category}") if tag else result
```

`metadata=` accepts a tuple of values with distinct exact Python types. Duplicate
types raise `ValueError`; base-class lookup is not implicit. `ToolMetadata.get(T)`
returns `T | None`. The collection is immutable, and callers must also keep its
values immutable. Metadata stays process-local: it is neither sent to the model
nor automatically persisted. Write an application event if audit storage is needed.

`ToolPolicy`, `ExecuteTool` and `ReviewToolResult` expose `tool: ToolInfo | None`.
The snapshot includes `definition`, `metadata`, `readonly`, `parallel` and `chain`
from the registration captured during planning. It never follows a replacement
registration by name. Calls without a registered tool have `tool=None`; synthetic
structured-answer results do not pass through these hooks. Import `ToolInfo`,
`ToolMetadata` and `ToolChain` from `harnessed` when annotating consumers.

Direct registry registration accepts the same metadata and chain options.
Schema-defined integrations use `register_external(..., spec=ToolSpec(metadata=...))`
and receive the same captured descriptions, without introducing provider metadata
into the model-facing `ToolDefinition`.

## End an action chain

One model response may contain several calls. A context-changing tool can stop
the remaining calls from that response while keeping the turn open:

```python
from harnessed import Plugin, ToolChain, ToolResult, tool


class Workspace(Plugin):
    @tool(chain=ToolChain.STOP_AFTER)
    async def switch_context(self, context_id: str) -> str:
        return f"Active context: {context_id}"

    @tool
    async def refresh(self) -> ToolResult:
        return ToolResult("State changed; decide again.", break_chain=True)
```

The default is `ToolChain.CONTINUE`. `STOP_AFTER` runs alone, after earlier
parallel calls finish. Once its execution is attempted, later calls are skipped,
including when execution fails or times out. A denial or disabled tool does not
trigger the boundary because execution never starts. Combining `STOP_AFTER` with
`parallel=True` raises `ValueError`.

`break_chain=True` is a per-result signal. With sequential tools it skips the
remaining calls. With parallel tools it prevents further starts after the signal;
already-started invocations finish normally. Use a sequential tool for a hard
context boundary. A review hook may also request a break. Review, spill and output
limiting cannot clear a break already requested by execution.

Skipped calls receive `ToolStatus.SKIPPED` through the usual `ToolCallFinishedEvent`
and model history, in the original call order. They have no execution receipt and
do not request approval. Prior effects remain intact. Break signals are durable
in execution receipts and final results; recovery preserves skipped status for
unstarted calls after a recorded break. The next model step can decide again,
subject to the turn's existing limits.

## Hook signatures

`@hook(Event)` and `@scope.hook(Event)` derive the callback type from the event:

| Event kind | Callback |
| --- | --- |
| Emit / log event | `(event: E) -> None` |
| Bail | `(event: E) -> R | None` |
| Waterfall | `(event: E, next_: Next[R]) -> R` |

Async callbacks return those values after awaiting. Additional parameters use
`Inject[T]`. Class-based plugin methods follow the same signatures after `self`.
`Next[R]` is a zero-argument async continuation and may be called once.

Runtime hook events (`PreStep`, `PrepareRequest`, `PrepareRequest`,
`ToolPolicy`, `ExecuteTool`, `ReviewToolResult`, `RequestApproval`,
`TurnStopping` and `SessionClosing`) carry `event.session: SessionContext`, not
the application `Session`. The context exposes `id`, `status`, `log`, `messages`,
`last(Event)`, `inbox`, `approvals`, `tools`, `observe()`, `append_message()` and
`emit()`. It cannot start turns, mount plugins or close the session from inside
its own loop. Plugins that need the same view outside a hook declare
`SessionContext` from `harnessed.services` as a dependency; resolve other session
services through `Inject[T]`.

Each dispatched event has its own result contract. A generic waterfall lets one
middleware handle actions with independent result types:

```python
from dataclasses import dataclass
from typing import Protocol

from harnessed import Plugin
from harnessed.kernel import Next, Waterfall, hook


class Intent[R](Protocol):
    async def stage(self, next_: Next[R]) -> R: ...


@dataclass(frozen=True)
class Perform[R](Waterfall[R]):
    intent: Intent[R]


class ActionStage(Plugin):
    @hook(Perform)
    async def stage[R](self, event: Perform[R], next_: Next[R]) -> R:
        return await event.intent.stage(next_)
```

`R` is chosen separately for each event: a find intent can stage `list[str]`,
while a navigation intent stages `bool`. Each concrete intent implements its
own typed stage and can return a result directly or call its continuation.
Delegating through `Intent[R]` keeps the intent, continuation and result related.
Matching a generic event's intent by class does not let Python narrow its result
type and `Next[R]` together.

Tools retain independent parameter lists and native return types; `@tool`
preserves the decorated method's signature. An application wrapper can preserve
those arguments with a `ParamSpec` and bind them once before dispatch:

```python
from collections.abc import Awaitable, Callable

from harnessed.kernel import Scope


async def perform[**P, R](
    scope: Scope,
    intent: Intent[R],
    action: Callable[P, Awaitable[R]],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    async def terminal() -> R:
        return await action(*args, **kwargs)

    return await scope.dispatch(Perform(intent), default=terminal)
```

This async-action wrapper requires no shared tool argument schema or result
class. Fixed-result events such as `Waterfall[ToolResult]` and explicit result
unions remain useful where the application has that contract. `Any` opts out
of checking the affected types.

The generic middleware body and `scope.dispatch` retain `R`. The current hook
decorator specializes the callable it returns, so direct calls to a decorated
generic method do not retain the same generic inference. Keep typed application
entrypoints in functions such as `perform` and invoke middleware through dispatch.

`scope.dispatch(event, default=terminal)` and
`EventBus.dispatch(scope, event, default=terminal)` also check the result contract.
The zero-argument terminal may return the result directly or an awaitable of it.
The event's result type is invariant: a `Waterfall[str]` rejects an `int` terminal
instead of widening the dispatch result to `str | int`.

Pass several event classes to `@hook(...)` to share one handler:

```python
from harnessed import Plugin
from harnessed.events import CompactionFinishedEvent, CompactionStartedEvent
from harnessed.kernel import hook


class CompactionLog(Plugin):
    @hook(CompactionStartedEvent, CompactionFinishedEvent)
    def forward(self, event: CompactionStartedEvent | CompactionFinishedEvent) -> None:
        print(event.type)
```

The handler must accept every listed event, usually through a union annotation,
and return a value compatible with every event's callback contract. Shared
waterfall handlers also need compatible `Next[R]` continuations. Observer and
waterfall events cannot share a decorator. Sync and async handlers support
`Inject[T]` parameters as usual.

Each event key creates a separate listener, equivalent to stacked `@hook(Event)`
decorators. `priority` and `prepend` apply to every listener, and plugin disposal
removes them all. Repeated keys or overlapping base/subclass keys can call the
handler more than once for a dispatched event.

Ty checks wrong event types, wrong callback results and incompatible continuations
for decorators and direct `scope.on(Event, callback)` registrations. The CI consumer
fixtures include expected failures and reject unused suppressions, so weakening
a signature breaks those checks too. Equal priorities retain registration order;
lower priorities run first. Use `Priority.GUARD` for approval/policy gates and
`Priority.OBSERVE` for measurements around actual execution.

`harnessed.hooks.SessionAborting` lets an opt-in plugin drain background work before
an interruption repairs its log, including when the parent is idle. The hook carries
the owning `SessionLog`. Cleanup must be idempotent: abort and failure paths can both
dispatch it. A plugin should also own cleanup as a Scope effect for rollback.

`harnessed.hooks.SessionClosing` lets an opt-in plugin release live resources when
`session.aclose()` shuts down the driver, before plugin disposal. It receives the
closing session's `SessionContext` and runs once per explicit close. Also register cleanup with
`scope.effect(...)` to cover startup rollback, unmounting and context teardown.

## Multimodal results

A tool may explicitly return `ToolResult((TextPart("Page state"), ImagePart(url)))`.
The content sequence is frozen. Its `text` property projects only text; `with_note`
preserves part order and `with_text` replaces text while retaining images. Exit
codes, denial provenance, spill references and rule-match metadata survive both.

The output limit bounds textual characters while retaining images. Spill stores
large text and retains image parts alongside its preview. Request budgeting and
size rules include an approximate allowance of 1024 tokens per image; a provider
counter can replace the full request estimate. Images are not serialized into
text or silently discarded by those policies. Their actual token use depends on
the selected provider, image resolution and settings.

PydanticAI receives native multimodal tool-return content and handles providers
whose tool channel only accepts text. Image detail is forwarded; `original` maps
to `high` on the native adapter. The current neutral content types cover text and
images, not audio/video/documents.

Spill runs inside ordinary review hooks at `Priority.OBSERVE`, so a default-priority
rule can add a note to the final preview. Rule conditions inspect the original
execution result before lossy spill/limit transforms; their match bits travel with
the canonical result for replay.

## Refer to tools without repeating their wire names

Use decorated method references in policies and scripted model responses:

```python
from harnessed.plugins import Approvals, Filesystem
from harnessed.testing import ScriptedModel, call

model = ScriptedModel(
    [
        call(Filesystem.read_file, path="large.log", offset=0, limit=40),
        call(Filesystem.read_file, path="large.log", offset=40, limit=40),
        "Read the requested ranges.",
    ]
)
policy = Approvals(ask=[Filesystem.delete_file])
```

`call(method, **arguments)` validates the model arguments immediately, excluding
`self` and injected services. References honor `@tool(name=...)` and reject
arbitrary undecorated functions. For dynamic wire-level tests, string tool names
remain available. Reference arguments are schema-validated at runtime; this helper
does not claim static keyword checking through an arbitrary method's injected
parameters. File offsets and limits count Unicode characters, not bytes or lines.
