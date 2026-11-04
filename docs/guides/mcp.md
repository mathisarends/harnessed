# MCP tools

Install `harnessed[mcp]` and import `MCP` from `harnessed.plugins.mcp`.
The optional integration uses the standalone FastMCP client and its native
transports. Local Python tools continue to use Harnessed's own `@tool` decorator.
Each plugin recipe describes one server. Opening a session connects it, discovers
its tools and registers them on the normal Harnessed execution pipeline. Closing
the mount releases its tools, client and any subprocess it launched.

## Configure a server

```python
from harnessed import Agent
from harnessed.plugins.mcp import MCP

docs = MCP.http(
    "docs",
    url="https://docs.example.com/mcp",
    include_tools=("search", "read_document"),
    readonly_tools=("search", "read_document"),
)

agent = Agent(model="openai:gpt-5.4", plugins=[docs])
```

`http()` uses Streamable HTTP. It accepts `headers=` and FastMCP's `auth=` options:
a bearer-token string, `"oauth"` for its interactive OAuth helper, or an `httpx.Auth`
object such as a configured `fastmcp.client.auth.OAuth` or MCP `OAuthClientProvider`.
Configure token storage and interactive authorization callbacks in the application.
The plugin creates and closes its HTTP client for each mount.

Launch a local server with an explicit argument vector:

```python
import sys
from harnessed.plugins.mcp import MCP

project = MCP.stdio(
    "project",
    command=sys.executable,
    args=("tools_server.py",),
    cwd="/path/to/project",
    env={"PROJECT_MODE": "development"},
)
```

`stdio()` delegates process startup and shutdown to FastMCP's `StdioTransport`,
with `keep_alive=False` so each mount releases its owned subprocess. Its
`env` supplements the SDK's minimal inherited environment, rather than copying
the application's entire environment. It never joins arguments into a shell
command. Server stderr goes to the application's stderr.

Construction performs no I/O. Add several recipes to `plugins=[...]` to connect
several servers. Recipes can be reused across concurrent sessions; each mount
gets a separate connection and catalog.

## Names, selection and approvals

The model sees separate tools with the server's descriptions and JSON Schemas.
Server `docs` and remote tool `search` become `docs__search`. Server names use
ASCII letters, digits, underscores and hyphens, start with a letter or digit,
and have at most 32 characters. Remote punctuation is replaced with underscores;
names exceeding 64 characters use a deterministic hash suffix. Collisions fail
startup instead of replacing another tool.

`include_tools=None` exposes every discovered tool; `include_tools=()` exposes
none. Filters and `readonly_tools` use the original remote names. A configured
name missing from the complete paginated catalog fails startup before a model
request. Schemas are checked at mount time; arguments are validated without
coercion before network execution. Schema references never trigger downloads.

Server annotations such as `readOnlyHint` do not grant local trust. Set
`readonly_tools` only for tools your application trusts as read-only. Other
tools follow the existing [approval policy](approvals.md); with the default
policy they need a callback or a UI approval responder. Rules such as
`Approvals(require=["docs__search"])` use the model-facing, namespaced name.
Required approval still takes precedence over `readonly_tools`.

`enabled=` accepts the same bool or synchronous injectable predicate as
[`@tool(enabled=...)`](tools.md#conditional-tools). It controls all selected
tools, and is checked before approval and execution. `parallel=False` is the
default; enable parallel execution explicitly when the server supports it.
Runtime tool events, tracing, output limits and spill use the existing pipeline.

## Lifetimes and failures

`startup_timeout=10.0` bounds connection, initialization and catalog discovery.
`call_timeout=60.0` bounds each tool execution, including result validation.
Both must be finite positive numbers. Failed startup unwinds acquired resources.

Timeout or cancellation closes that mount's connection and hides its tools.
This also interrupts other calls using that connection when `parallel=True`.
MCP SDK 1.x does not expose the request identity needed for precise cancellation
through its public high-level API, so Harnessed closes its owned transport.
Remote cancellation is best-effort: an external operation may already have
completed. Calls are never automatically retried.

Protocol errors, including a terminated HTTP session or a crashed stdio server,
also close the connection when a call detects the failure. A tool's ordinary
`isError` result leaves the connection available. The built-in transports reject
malformed messages immediately instead of waiting for a call timeout. HTTP session
termination uses a five-second network inactivity timeout, so an unresponsive
DELETE endpoint does not indefinitely hold up mount cleanup.

The tool catalog is a snapshot for the mount's lifetime. To reconnect or discover
changed tools, remove and mount a new recipe:

```python
async with agent.session() as session:
    handle = await session.use(project)
    result = await session.run("Inspect the project.")
    await handle.aclose()
```

A prepared call retains its registration identity. Removing or replacing the
plugin prevents that call from silently using a replacement server. Reopening
a stored session makes a fresh connection; replay reads recorded results without
executing completed remote operations again.

## Results and extension points

Text and images map to the existing Harnessed content parts. `structuredContent`
is preserved as a JSON text part alongside any textual summary, and checked
against the tool's declared output schema. `isError` becomes an error result;
protocol failures use the normal tool error formatter. Unsupported audio,
resource links and embedded resource blocks fail explicitly. This first version
exposes tools; resources, prompts, sampling and elicitation remain separate future
capabilities. Server instructions are not automatically added to the system prompt.

## Deployment boundary

Configure server endpoints, commands and trusted read-only tools in application
code. A stdio server runs with the application's OS permissions; its minimal
inherited environment is not a sandbox. Use process or container isolation for
servers that need restricted access to files, credentials or the network.

Use HTTPS and scoped authentication for remote production servers. If endpoints
or OAuth discovery URLs can come from outside the application, enforce outbound
network policy to prevent access to unintended internal services. The SDK rejects
cross-origin MCP redirects rather than forwarding credentials to another origin.

Tool descriptions and results remain untrusted model input. Schema validation
and approval policies enforce call contracts and permissions; they do not make
server-provided text safe instructions. The normal output limit bounds retained
result text, not transport bytes, image data or JSON Schema validation CPU time.
Apply resource limits at the process or deployment boundary when connecting to
untrusted servers.

## Custom transports

Use a standalone FastMCP server directly for tests or embedded applications:

```python
from fastmcp import FastMCP

from harnessed.plugins.mcp import MCP

server = FastMCP("documentation")


@server.tool
def search(query: str) -> str:
    return f"Documentation for {query}"


docs = MCP.in_memory("docs", server=server, readonly_tools=("search",))
```

`in_memory()` has the same filtering, timeouts, approvals and lifecycle as HTTP
and stdio, without opening sockets or starting a subprocess.

For other FastMCP transports, construct `MCP(name, transport=recipe)`. The exported
`MCPTransport` protocol requires `create() -> fastmcp.client.transports.ClientTransport`.
Each call must return a fresh transport: FastMCP transports can carry mutable
connection state and must not be shared between mounts. FastMCP owns initialization
and the session task; Harnessed owns the mount, availability and cleanup.

```python
from fastmcp.client.transports import ClientTransport, StreamableHttpTransport


class DocsTransport:
    def create(self) -> ClientTransport:
        return StreamableHttpTransport("https://docs.example.com/mcp")


docs = MCP("docs", transport=DocsTransport())
```

Run `uv run --extra mcp python -m examples.31_mcp` for a complete offline example
using a real FastMCP stdio server and a scripted model. See the upstream
[FastMCP client](https://gofastmcp.com/clients/client) and
[transport documentation](https://gofastmcp.com/clients/transports) for the native
transport and authentication APIs.
