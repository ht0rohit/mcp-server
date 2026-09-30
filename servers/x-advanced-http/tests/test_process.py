"""Smoke tests that start the real server process on a port, as you would run it."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from collections.abc import Iterator

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

TOKEN = "smoke-test-token-0123456789"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running_server() -> Iterator[str]:
    port = free_port()
    env = {
        **os.environ,
        # Dummy X tokens: listing tools and the health check never call X.
        "X_BEARER_TOKEN": "dummy",
        "X_USER_ACCESS_TOKEN": "dummy",
        "MCP_AUTH_TOKENS": f"{TOKEN}=x:read,x:write",
        "MCP_PORT": str(port),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "x_advanced_http"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def wait_until_healthy(base: str) -> None:
    async with httpx2.AsyncClient() as http:
        with anyio.fail_after(20):
            while True:
                try:
                    if (await http.get(f"{base}/healthz")).status_code == 200:
                        return
                except httpx2.TransportError:
                    pass
                await anyio.sleep(0.1)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_real_server_over_http(running_server, mode):
    await wait_until_healthy(running_server)
    url = f"{running_server}/mcp"
    async with httpx2.AsyncClient() as http:
        assert (await http.post(url, json={})).status_code == 401

    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with (
        httpx2.AsyncClient(headers=headers) as http_client,
        Client(streamable_http_client(url, http_client=http_client), mode=mode) as client,
    ):
        assert client.server_info.name == "x-advanced-http"
        tools = {t.name for t in (await client.list_tools()).tools}
        assert {"x_search_digest", "x_create_post"} <= tools


def test_refuses_to_start_without_auth_tokens():
    env = {"PATH": os.environ.get("PATH", ""), "X_BEARER_TOKEN": "dummy"}
    command = [sys.executable, "-m", "x_advanced_http"]
    proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 1
    assert "MCP_AUTH_TOKENS" in proc.stderr
