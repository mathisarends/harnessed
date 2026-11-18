import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Coroutine, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Protocol, Unpack

import httpx2
from fastmcp import Client
from fastmcp.client.transports import (
    ClientTransport,
    StdioTransport,
    StreamableHttpTransport,
)
from fastmcp.client.transports.base import SessionKwargs, TransportOptions
from mcp import ClientSession
from mcp.types import ServerNotification

from harnessed.tools import ToolError

logger = logging.getLogger(__name__)


class MCPTransport(Protocol):
    """Create a fresh FastMCP transport for each independently owned mount."""

    def create(self) -> ClientTransport:
        """Return a fresh transport whose connection lifecycle belongs to one mount."""
        ...


@dataclass(frozen=True)
class Stdio:
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
class HTTP:
    url: str
    headers: Mapping[str, str] = field(repr=False)
    auth: httpx2.Auth | str | None = field(default=None, repr=False)

    def create(self) -> ClientTransport:
        return StreamableHttpTransport(
            self.url, headers=dict(self.headers), auth=self.auth, httpx_client_factory=_http_client
        )


class Connection:
    def __init__(self, transport: MCPTransport, name: str) -> None:
        self.name = name
        self.closed = asyncio.Event()
        self.closing: asyncio.Task[None] | None = None
        self.client = Client(
            _MonitoredTransport(transport.create(), self.closed),
            name=name,
            message_handler=self._check_message,
            # Multi-round input is unsupported; never reissue an approved tool call.
            input_required_max_rounds=0,
        )

    async def start(self) -> Client:
        return await self.request(self.client.__aenter__())

    async def request[T](self, operation: Coroutine[object, object, T]) -> T:
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

    async def _check_message(self, message: ServerNotification | Exception) -> None:
        # SDK v2 logs handler failures; signal our own waiters on malformed input.
        if isinstance(message, Exception):
            self.closed.set()

    async def _close_client(self) -> None:
        try:
            await self.client.close()
        except Exception as error:
            # FastMCP may re-raise an already reported session failure on close.
            logger.warning("MCP connection %s closed (%s)", self.name, type(error).__name__)
        finally:
            await self.client.transport.close()


async def _bound_http_shutdown(request: httpx2.Request) -> None:
    # SSE reads may stay open indefinitely; the SDK's session DELETE must not.
    if request.method == "DELETE":
        request.extensions["timeout"] = httpx2.Timeout(_HTTP_CLOSE_TIMEOUT_SECONDS).as_dict()


async def _check_http_response(response: httpx2.Response) -> None:
    # SDK v2 ignores unmatched response IDs; reject them before a waiter can stall.
    if response.headers.get("content-type", "").partition(";")[0] != "application/json":
        return
    request = response.request
    if request.headers.get("content-type", "").partition(";")[0] != "application/json":
        return
    request_body = await request.aread()
    sent = json.loads(request_body)
    if not isinstance(sent, dict) or "id" not in sent:
        return
    response_body = await response.aread()
    received = json.loads(response_body)
    if (
        isinstance(received, dict)
        and received.get("jsonrpc") == "2.0"
        and received.get("id") != sent["id"]
    ):
        raise ValueError("MCP HTTP response has an unexpected request id")


def _http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    *,
    follow_redirects: bool = False,
) -> httpx2.AsyncClient:
    # Keep the application's redirect boundary (never follow redirects) and bounded
    # HTTP termination.
    del follow_redirects
    return httpx2.AsyncClient(
        headers=headers,
        auth=auth,
        timeout=timeout if timeout is not None else httpx2.Timeout(30, read=None),
        event_hooks={"request": [_bound_http_shutdown], "response": [_check_http_response]},
    )


class _MonitoredTransport(ClientTransport):
    def __init__(self, transport: ClientTransport, closed: asyncio.Event) -> None:
        self.transport = transport
        self.legacy_only = transport.legacy_only
        self.closed = closed

    @asynccontextmanager
    async def connect_session(
        self,
        *,
        transport_options: TransportOptions | None = None,
        **session_kwargs: Unpack[SessionKwargs],
    ) -> AsyncGenerator[ClientSession]:
        try:
            async with self.transport.connect_session(
                transport_options=transport_options, **session_kwargs
            ) as session:
                self.legacy_only = self.transport.legacy_only
                yield session
        finally:
            self.closed.set()

    def get_session_id(self) -> str | None:
        return self.transport.get_session_id()

    async def close(self) -> None:
        await self.transport.close()


_HTTP_CLOSE_TIMEOUT_SECONDS = 5.0
