"""Smoke tests that run the server as a real subprocess over stdio, as a client would."""

from __future__ import annotations

import subprocess
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.anyio


async def test_stdio_handshake_and_list_tools():
    # A dummy token is enough: listing tools, resources and prompts never calls X.
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "x_basic_stdio"], env={"X_BEARER_TOKEN": "dummy"}
    )
    async with Client(params) as client:
        assert client.server_info.name == "x-basic-stdio"
        tools = (await client.list_tools()).tools
        assert len(tools) == 7
        assert (await client.list_prompts()).prompts


def test_exits_with_a_clear_message_without_a_token():
    env = {"PATH": "", "X_BEARER_TOKEN": ""}
    proc = subprocess.run(
        [sys.executable, "-m", "x_basic_stdio"], env=env, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 1
    assert "X_BEARER_TOKEN" in proc.stderr
    assert proc.stdout == ""  # stdout belongs to the protocol
