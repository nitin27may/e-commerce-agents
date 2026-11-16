"""MAF's ``MCPStreamableHTTPTool`` against a real MCP 2.x server, over a real socket.

Covers what the B-6 upgrade changed on the client side: the tool's ``http_client`` must be an
``httpx2.AsyncClient`` (what ``shared.oauth.service_client.build_mcp_http_client`` now returns), the
bearer header must cover the initialize/list-tools handshake and not only ``call_tool``, and the
default request timeout and discovery limits added in core 1.25 must not get in the way of a normal
session. No DB, no LLM.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from contextlib import asynccontextmanager

import httpx2
import pytest
import uvicorn
from agent_framework import MCPStreamableHTTPTool
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

from shared.oauth.service_client import build_mcp_http_client, set_mcp_auth_header

_TOKEN = "good-token"


class _Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token != _TOKEN:
            return None
        return AccessToken(token=token, client_id="test", scopes=["mcp:test"])


@asynccontextmanager
async def _noop_lifespan(_server):
    yield


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mcp_url():
    server = MCPServer(
        "client-test-mcp",
        lifespan=_noop_lifespan,
        token_verifier=_Verifier(),
        auth=AuthSettings(
            issuer_url="http://127.0.0.1:1",
            resource_server_url="http://127.0.0.1:9/mcp",
            required_scopes=["mcp:test"],
            validate_token_resource=False,
        ),
    )

    @server.tool()
    def ping(who: str) -> str:
        """Say hello."""
        return f"pong {who}"

    port = _free_port()
    uv = uvicorn.Server(
        uvicorn.Config(server.streamable_http_app(host="0.0.0.0"), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert uv.started, "test MCP server did not start"
    yield f"http://127.0.0.1:{port}/mcp"
    uv.should_exit = True
    thread.join(timeout=5)


def test_build_mcp_http_client_is_httpx2() -> None:
    client = build_mcp_http_client()
    assert isinstance(client, httpx2.AsyncClient)
    set_mcp_auth_header(client, "abc")
    assert client.headers["Authorization"] == "Bearer abc"


async def test_tool_discovers_and_calls_with_bearer_on_handshake(mcp_url) -> None:
    client = build_mcp_http_client()
    set_mcp_auth_header(client, _TOKEN)
    tool = MCPStreamableHTTPTool(name="test-mcp", url=mcp_url, http_client=client)
    async with tool:
        assert [f.name for f in tool.functions] == ["ping"]
        result = await asyncio.wait_for(tool.call_tool("ping", who="x"), timeout=10)
    assert [c.text for c in result] == ['{"result": "pong x"}']  # structured_first, as at core 1.22
    await client.aclose()


async def test_tool_without_bearer_cannot_connect(mcp_url) -> None:
    client = build_mcp_http_client()
    tool = MCPStreamableHTTPTool(name="test-mcp", url=mcp_url, http_client=client)
    with pytest.raises(Exception):  # noqa: B017 - the exact exception type is the SDK's business
        async with tool:
            pass
    await client.aclose()
