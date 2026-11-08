# Consume MCP server tools

Install `harnessed[mcp]` and import `MCP` from `harnessed.plugins.mcp`.
The optional integration uses FastMCP 4 (`fastmcp-slim[client]`) with MCP SDK 2
and its native client transports to consume existing MCP servers. Harnessed does not host MCP servers
or publish local tools over MCP. Local Python tools use Harnessed's own `@tool`
decorator and remain inside the agent runtime.
Each plugin recipe describes one server. Opening a session connects it, discovers
its tools and registers them on the normal Harnessed execution pipeline. Closing
the mount releases its tools, client and any subprocess it launched.

## Connect to a server

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
a bearer-token string, `"oauth"` for its interactive OAuth helper, or an `httpx2.Auth`
object such as a configured `fastmcp.client.auth.OAuth` or MCP `OAuthClientProvider`.
Configure token storage and interactive authorization callbacks in the application.
MCP SDK 2 callback handlers return `mcp.shared.auth.AuthorizationCodeResult`
with `code`, `state`, and the optional authorization-response `iss`; replace
the old `(code, state)` tuple return.
The plugin creates and closes its HTTP client for each mount. The client negotiates
the current protocol automatically and falls back to legacy initialization for
older servers. Modern HTTP connections are sessionless; legacy stateful sessions
still send a bounded DELETE request on close.

For machine-to-machine OAuth, bind pre-registered credentials to their authorization
server with `issuer=`; upgrading dependencies alone does not enable this protection:

```python
from fastmcp.client.auth import ClientCredentialsOAuthProvider

auth = ClientCredentialsOAuthProvider(
    client_id="docs-client",
    client_secret="application-provided-secret",
    issuer="https://auth.example.com",
)
docs = MCP.http("docs", url="https://docs.example.com/mcp", auth=auth)
```

Set `issuer=` on `PrivateKeyJWTOAuthProvider` as well. When migrating stored OAuth
client registrations from MCP SDK versions older than 1.30, clear unbound client
information once and register again, or bind a pre-registered record to its issuer.
If credentials may already have reached an untrusted server, rotate client secrets
and revoke the associated tokens.

Connect to an installed stdio MCP server using its command and argument vector:

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
`Approvals(ask=["docs__search"])` use the model-facing, namespaced name.
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
Harnessed closes its owned transport so all calls sharing the failed mount
observe the same unavailable connection.
Remote cancellation is best-effort: an external operation may already have
completed. Calls are never automatically retried. A multi-round
`InputRequiredResult` fails explicitly instead of repeating an approved tool call.

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
    handle = await session.mount(project)
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

Run `uv run --extra mcp python -m examples.31_mcp --url http://localhost:8000/mcp`
to consume an existing read-only `search(query: str)` tool with a scripted model.
The example requires an externally supplied MCP endpoint. See the upstream
[FastMCP client](https://gofastmcp.com/clients/client) and
[transport documentation](https://gofastmcp.com/clients/transports) for the native
transport and authentication APIs.
