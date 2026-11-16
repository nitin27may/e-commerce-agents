"""The installed console script starts and speaks MCP (the thing `uvx ecommerce-mcp-product` runs).

Two tiers, like the server tests:
- ``main()`` argument handling, no process, no DB.
- Integration: the real console script as a subprocess against the testcontainer Postgres -- once over
  streamable HTTP, once over stdio -- and an MCP ``initialize`` + ``tools/list`` + a tool call through the
  SDK's own client.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from ecommerce_mcp_product import server

SCRIPT = "ecommerce-mcp-product"
DEFAULT_PORT = "9000"
SERVER_NAME = "product-discovery-mcp"
TOOL_NAMES = {
    "search_products",
    "get_product_details",
    "compare_products",
    "get_trending_products",
    "get_price_history",
}
CALL = ("search_products", {"query": "nothing-matches-this", "limit": 1})

# ─────────────────────── main() argument handling ───────────────────────────


def test_main_defaults_to_http_on_the_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    seen: dict = {}
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda target, **kw: seen.update(target=target, **kw))

    server.main([])

    assert seen == {"target": "ecommerce_mcp_product.server:app", "host": "0.0.0.0", "port": int(DEFAULT_PORT)}


def test_main_honours_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    seen: dict = {}
    monkeypatch.setenv("PORT", "9123")
    monkeypatch.setattr(uvicorn, "run", lambda target, **kw: seen.update(kw))

    server.main(["--transport", "http"])

    assert seen["port"] == 9123


@pytest.mark.parametrize("how", ["flag", "env"])
def test_main_stdio_runs_the_mcp_stdio_loop_and_not_uvicorn(monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    import uvicorn

    calls: list[str] = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **kw: calls.append("stdio"))
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append("http"))
    if how == "env":
        monkeypatch.setenv("MCP_TRANSPORT", "stdio")
        server.main([])
    else:
        monkeypatch.delenv("MCP_TRANSPORT", raising=False)
        server.main(["--transport", "stdio"])

    assert calls == ["stdio"]


def test_main_rejects_an_unknown_transport() -> None:
    with pytest.raises(SystemExit):
        server.main(["--transport", "carrier-pigeon"])


# ─────────────────────── the real console script ────────────────────────────


def _script() -> list[str]:
    """The console script of the environment running the tests; the module's main() when it is absent."""
    candidate = Path(sys.executable).parent / SCRIPT
    if candidate.exists():
        return [str(candidate)]
    return [sys.executable, "-c", "from ecommerce_mcp_product.server import main; main()"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _exercise(session: ClientSession) -> None:
    init = await session.initialize()
    assert init.server_info.name == SERVER_NAME
    assert {t.name for t in (await session.list_tools()).tools} == TOOL_NAMES
    result = await session.call_tool(*CALL)
    assert not result.is_error


@asynccontextmanager
async def _http_server(database_url: str) -> AsyncIterator[str]:
    port = _free_port()
    env = {**os.environ, "DATABASE_URL": database_url, "PORT": str(port), "MCP_AUTH_ENABLED": "false"}
    command, *args = _script()
    proc = await asyncio.create_subprocess_exec(
        command, *args, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                raise AssertionError(f"{SCRIPT} exited early with status {proc.returncode}")
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError(f"{SCRIPT} did not open port {port} in 30s")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except TimeoutError:
                proc.kill()
                await proc.wait()


@pytest.mark.integration
async def test_console_script_serves_mcp_over_streamable_http(database_url: str, postgres_pool: asyncpg.Pool) -> None:
    async with (
        _http_server(database_url) as url,
        streamable_http_client(url) as (read, write),
        ClientSession(read, write) as session,
    ):
        await _exercise(session)


@pytest.mark.integration
async def test_console_script_serves_mcp_over_stdio(database_url: str, postgres_pool: asyncpg.Pool) -> None:
    command, *args = _script()
    params = StdioServerParameters(
        command=command, args=[*args, "--transport", "stdio"], env={**os.environ, "DATABASE_URL": database_url}
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await _exercise(session)
