import asyncio
import json
import logging
import re
import warnings
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine, Iterable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Annotated, NoReturn, Protocol, Self, Unpack

import httpx
from fastmcp import Client
from fastmcp.client.transports import (
    ClientTransport,
    StdioTransport,
    StreamableHttpTransport,
)
from fastmcp.client.transports.base import SessionKwargs
from jsonschema import Draft202012Validator, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from mcp import ClientSession
from mcp.shared.session import RequestResponder
from mcp.types import (
    CallToolResult,
    ClientResult,
    ImageContent,
    ServerNotification,
    ServerRequest,
    TextContent,
    Tool,
)
from pydantic import Field
from pydantic import ValidationError as ModelValidationError
from referencing import Registry
from referencing.exceptions import NoSuchResource

from harnessed.core.messages import (
    ContentPart,
    ImagePart,
    TextPart,
    ToolResult,
    ToolStatus,
    to_dict,
)
from harnessed.core.model import ToolDefinition
from harnessed.core.plugin import Plugin, PluginConfig
from harnessed.core.tools import (
    ToolAnnotations,
    ToolContext,
    ToolError,
    ToolIcon,
    ToolPresentation,
    ToolRegistry,
    ToolSpec,
)
from harnessed.core.tools.enabled import BoundToolEnabled, ToolEnabled, bind_enabled
from harnessed.kernel import PluginScope, RegistrationState

logger = logging.getLogger(__name__)

_HTTP_CLOSE_TIMEOUT = 5.0


async def _bound_http_shutdown(request: httpx.Request) -> None:
    # SSE reads may stay open indefinitely; the SDK's session DELETE must not.
    if request.method == "DELETE":
        request.extensions["timeout"] = httpx.Timeout(_HTTP_CLOSE_TIMEOUT).as_dict()


async def _check_message(
    message: RequestResponder[ServerRequest, ClientResult] | ServerNotification | Exception,
) -> None:
    # The SDK's default handler ignores malformed messages, leaving pending
    # requests waiting until timeout. Fail the receive loop and wake its waiters.
    if isinstance(message, Exception):
        raise message


class MCPTransport(Protocol):
    """Create a fresh FastMCP transport for each independently owned mount."""

    def create(self) -> ClientTransport: ...


@dataclass(frozen=True)
class _Stdio:
    command: str
    args: tuple[str, ...]
    env: Mapping[str, str] | None
    cwd: str | None

    def create(self) -> ClientTransport:
        return StdioTransport(
            command=self.command,
            args=list(self.args),
            env=None if self.env is None else dict(self.env),
            cwd=self.cwd,
            keep_alive=False,
        )


@dataclass(frozen=True)
class _HTTP:
    url: str
    headers: Mapping[str, str] = field(repr=False)
    auth: httpx.Auth | str | None = field(default=None, repr=False)

    def create(self) -> ClientTransport:
        return StreamableHttpTransport(
            self.url, headers=dict(self.headers), auth=self.auth, httpx_client_factory=_http_client
        )


def _http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
    *,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    # Keep the application's redirect boundary and bounded HTTP termination.
    return httpx.AsyncClient(
        headers=headers,
        auth=auth,
        timeout=timeout if timeout is not None else httpx.Timeout(30, read=None),
        event_hooks={"request": [_bound_http_shutdown]},
    )


class MCPConfig(PluginConfig):
    server_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=32)]
    include_tools: tuple[str, ...] | None = None
    readonly_tools: tuple[str, ...] = ()
    startup_timeout: Annotated[float, Field(gt=0)] = 10.0
    call_timeout: Annotated[float, Field(gt=0)] = 60.0
    parallel: bool = False


class _Connection:
    def __init__(self, transport: MCPTransport, name: str) -> None:
        self.name = name
        self.closed = asyncio.Event()
        self.closing: asyncio.Task[None] | None = None
        self.client = Client(
            _MonitoredTransport(transport.create(), self.closed),
            name=name,
            message_handler=_check_message,
        )

    async def start(self) -> Client:
        return await self.client.__aenter__()

    async def request(self, operation: Coroutine[object, object, CallToolResult]) -> CallToolResult:
        # SDK shutdown can cancel its receive loop before it wakes every waiter.
        request = asyncio.create_task(operation, name=f"harnessed:mcp:{self.name}:request")
        closed = asyncio.create_task(self.closed.wait(), name=f"harnessed:mcp:{self.name}:closed")
        try:
            done, _ = await asyncio.wait((request, closed), return_when=asyncio.FIRST_COMPLETED)
            if request in done:
                return request.result()
            raise ToolError("MCP connection closed; remount the plugin to reconnect")
        finally:
            request.cancel()
            closed.cancel()
            await asyncio.gather(request, closed, return_exceptions=True)

    async def aclose(self) -> None:
        if self.closing is None:
            self.closed.set()
            self.closing = asyncio.create_task(
                self._close_client(), name=f"harnessed:mcp:{self.name}:close"
            )
        while not self.closing.done():
            try:
                await asyncio.shield(self.closing)
            except asyncio.CancelledError:
                continue
        with suppress(asyncio.CancelledError):
            self.closing.result()

    @property
    def active(self) -> bool:
        return not self.closed.is_set() and self.client.is_connected()

    async def _close_client(self) -> None:
        try:
            await self.client.close()
        except Exception as error:
            # FastMCP may re-raise an already reported session failure on close.
            logger.warning("MCP connection %s closed (%s)", self.name, type(error).__name__)
        finally:
            await self.client.transport.close()


class _MonitoredTransport(ClientTransport):
    def __init__(self, transport: ClientTransport, closed: asyncio.Event) -> None:
        self.transport = transport
        self.closed = closed

    @asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[SessionKwargs]
    ) -> AsyncGenerator[ClientSession]:
        try:
            async with self.transport.connect_session(**session_kwargs) as session:
                yield session
        finally:
            self.closed.set()

    def get_session_id(self) -> str | None:
        return self.transport.get_session_id()

    async def close(self) -> None:
        await self.transport.close()


def _no_remote_schema(uri: str) -> NoReturn:
    raise NoSuchResource(ref=uri)


def _validator(schema: Mapping[str, object]) -> Validator:
    # Schema validation must never fetch URLs supplied by a remote server.
    value = to_dict(schema)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            kind = validator_for(value) if "$schema" in value else Draft202012Validator
    except DeprecationWarning as error:
        raise ValueError("Unsupported MCP JSON Schema dialect") from error
    kind.check_schema(value)
    return kind(value, registry=Registry(retrieve=_no_remote_schema))


def _presentation(tool: Tool) -> ToolPresentation:
    meta = tool.meta if tool.meta is not None else {}
    fastmcp_meta = meta.get("fastmcp")
    tags = fastmcp_meta.get("tags", ()) if isinstance(fastmcp_meta, Mapping) else ()
    return ToolPresentation(
        title=tool.title,
        tags=tags,
        icons=tuple(
            ToolIcon(
                src=icon.src,
                mime_type=icon.mimeType,
                sizes=tuple(icon.sizes if icon.sizes is not None else ()),
            )
            for icon in (tool.icons if tool.icons is not None else ())
        ),
        annotations=None
        if tool.annotations is None
        else ToolAnnotations(
            read_only_hint=tool.annotations.readOnlyHint,
            destructive_hint=tool.annotations.destructiveHint,
            idempotent_hint=tool.annotations.idempotentHint,
            open_world_hint=tool.annotations.openWorldHint,
        ),
        meta=meta,
    )


def _tool_name(server: str, remote: str) -> str:
    name = f"{server}__{re.sub(r'[^A-Za-z0-9_-]', '_', remote)}"
    if len(name) > 64:
        name = f"{name[:51]}_{sha256(remote.encode()).hexdigest()[:12]}"
    return name


def _result(result: CallToolResult) -> ToolResult:
    parts: list[ContentPart] = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(TextPart(block.text))
        elif isinstance(block, ImageContent):
            parts.append(ImagePart(f"data:{block.mimeType};base64,{block.data}"))
        else:
            raise ToolError(f"Unsupported MCP result content: {block.type}")
    if result.structuredContent is not None:
        # Keep server JSON even when the server also supplies a textual summary.
        text = json.dumps(result.structuredContent, ensure_ascii=False, allow_nan=False)
        if not any(isinstance(part, TextPart) and part.text == text for part in parts):
            parts.append(TextPart(text))
    return ToolResult(tuple(parts), ToolStatus.ERROR if result.isError else ToolStatus.OK)


@dataclass(frozen=True)
class _RemoteTool:
    connection: _Connection
    client: Client
    tool: Tool
    input_validator: Validator
    output_validator: Validator | None

    async def invoke(self, arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        if not self.connection.active:
            return ToolResult.unavailable("MCP connection closed; remount the plugin to reconnect")
        payload = to_dict(arguments)
        try:
            self.input_validator.validate(payload)
        except ValidationError as error:
            raise ToolError(f"Invalid MCP tool arguments: {error.message}") from error

        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            text = message or (f"{progress}/{total}" if total is not None else str(progress))
            await context.progress(text)

        try:
            result = await self.connection.request(
                self.client.call_tool_mcp(self.tool.name, payload, progress_handler=on_progress)
            )
        except ModelValidationError as error:
            await self.connection.aclose()
            raise ToolError("MCP server returned an invalid tool response") from error
        except (asyncio.CancelledError, Exception):
            # Calls are never retried. Fail closed across the mount, including
            # parallel waiters, when a request is cancelled or its protocol fails.
            await self.connection.aclose()
            raise
        if not result.isError and self.output_validator is not None:
            if result.structuredContent is None:
                raise ToolError("MCP tool omitted its declared structured output")
            try:
                self.output_validator.validate(result.structuredContent)
            except ValidationError as error:
                raise ToolError("MCP tool returned invalid structured output") from error
        return _result(result)


class MCP(Plugin[MCPConfig], needs=(ToolRegistry,)):
    """Consume one external MCP server's tools through the Harnessed runtime.

    Construction performs no I/O. Each session or runtime mount owns a fresh
    connection and a fixed tool catalog. Server annotations never grant local
    readonly status; configure trusted tools explicitly with readonly_tools.
    """

    _check_enabled: BoundToolEnabled

    def __init__(
        self,
        name: str,
        *,
        transport: MCPTransport,
        include_tools: Iterable[str] | None = None,
        readonly_tools: Iterable[str] = (),
        startup_timeout: float = 10.0,
        call_timeout: float = 60.0,
        parallel: bool = False,
        enabled: ToolEnabled = True,
    ) -> None:
        if isinstance(include_tools, str) or isinstance(readonly_tools, str):
            raise TypeError("MCP tool selections must be collections of names, not strings")
        super().__init__(
            name=f"mcp:{name}",
            config=MCPConfig(
                server_name=name,
                include_tools=None if include_tools is None else tuple(include_tools),
                readonly_tools=tuple(readonly_tools),
                startup_timeout=startup_timeout,
                call_timeout=call_timeout,
                parallel=parallel,
            ),
        )
        ToolSpec(enabled=enabled)
        self.transport = transport
        self.enabled = enabled

    @classmethod
    def stdio(
        cls,
        name: str,
        *,
        command: str,
        args: Iterable[str] = (),
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        include_tools: Iterable[str] | None = None,
        readonly_tools: Iterable[str] = (),
        startup_timeout: float = 10.0,
        call_timeout: float = 60.0,
        parallel: bool = False,
        enabled: ToolEnabled = True,
    ) -> Self:
        """Consume an installed stdio server through a client-owned subprocess."""
        if isinstance(args, str):
            raise TypeError("MCP process args must be a collection, not a command string")
        transport = _Stdio(
            command,
            tuple(args),
            None if env is None else dict(env),
            None if cwd is None else str(cwd),
        )
        return cls(
            name,
            transport=transport,
            include_tools=include_tools,
            readonly_tools=readonly_tools,
            startup_timeout=startup_timeout,
            call_timeout=call_timeout,
            parallel=parallel,
            enabled=enabled,
        )

    @classmethod
    def http(
        cls,
        name: str,
        *,
        url: str,
        headers: Mapping[str, str] | None = None,
        auth: httpx.Auth | str | None = None,
        include_tools: Iterable[str] | None = None,
        readonly_tools: Iterable[str] = (),
        startup_timeout: float = 10.0,
        call_timeout: float = 60.0,
        parallel: bool = False,
        enabled: ToolEnabled = True,
    ) -> Self:
        """Connect over HTTP with FastMCP bearer, OAuth or custom httpx authentication."""
        parsed = httpx.URL(url)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            raise ValueError("MCP HTTP URL must have an http(s) scheme and host")
        return cls(
            name,
            transport=_HTTP(url, dict(headers if headers is not None else {}), auth),
            include_tools=include_tools,
            readonly_tools=readonly_tools,
            startup_timeout=startup_timeout,
            call_timeout=call_timeout,
            parallel=parallel,
            enabled=enabled,
        )

    async def setup(self, scope: PluginScope) -> None:
        connection = _Connection(self.transport, self.config.server_name)

        def install(registration: RegistrationState) -> Callable[[], Awaitable[None]]:
            self._check_enabled = bind_enabled(self.enabled, self.setup, registration)
            return connection.aclose

        scope.register(install)
        async with asyncio.timeout(self.config.startup_timeout):
            client = await connection.start()
            catalog: dict[str, Tool] = {}
            cursor: str | None = None
            seen_cursors: set[str] = set()
            while True:
                page = await client.list_tools_mcp(cursor=cursor)
                for tool in page.tools:
                    if tool.name in catalog:
                        raise ValueError(f"Duplicate MCP tool: {tool.name!r}")
                    catalog[tool.name] = tool
                cursor = page.nextCursor
                if cursor is None:
                    break
                if cursor in seen_cursors:
                    raise ValueError("MCP tools/list repeated a pagination cursor")
                seen_cursors.add(cursor)
        selected = self.config.include_tools
        requested = set(selected or ()) | set(self.config.readonly_tools)
        if missing := requested - catalog.keys():
            raise ValueError(f"MCP server omitted configured tools: {sorted(missing)}")
        registry = scope[ToolRegistry]
        for name, tool in catalog.items():
            if selected is not None and name not in selected:
                continue
            remote = _RemoteTool(
                connection,
                client,
                tool,
                _validator(tool.inputSchema),
                None if tool.outputSchema is None else _validator(tool.outputSchema),
            )
            registry.register_external(
                ToolDefinition(
                    _tool_name(self.config.server_name, name),
                    tool.description or "",
                    tool.inputSchema,
                ),
                remote.invoke,
                owner=scope,
                spec=ToolSpec(
                    timeout_seconds=self.config.call_timeout,
                    parallel=self.config.parallel,
                    readonly=name in self.config.readonly_tools,
                    enabled=lambda: connection.active and self._check_enabled(scope),
                    presentation=_presentation(tool),
                ),
            )


__all__ = ["MCP", "MCPConfig", "MCPTransport"]
