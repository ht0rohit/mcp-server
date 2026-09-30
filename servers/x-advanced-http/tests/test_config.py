"""Configuration fails fast, naming the variable to fix."""

from __future__ import annotations

import pytest
from x_advanced_http.client import UserCache
from x_advanced_http.config import Config, ConfigError

BASE = {"X_BEARER_TOKEN": "t", "MCP_AUTH_TOKENS": "a-token-of-16-chars=x:read"}


def test_reads_tokens_and_scopes():
    config = Config.from_env(
        {**BASE, "MCP_AUTH_TOKENS": "reader-0123456789=x:read; writer-0123456789=x:read,x:write"}
    )
    assert config.auth_tokens == {
        "reader-0123456789": {"x:read"},
        "writer-0123456789": {"x:read", "x:write"},
    }
    assert config.public_url == "http://127.0.0.1:8000/mcp"
    assert not config.can_write


def test_cli_port_moves_the_default_public_url():
    config = Config.from_env({**BASE, "MCP_PORT": "8000"}, port=9000, host="0.0.0.0")
    assert (config.port, config.host) == (9000, "0.0.0.0")
    assert config.public_url == "http://127.0.0.1:9000/mcp"
    # An explicit public URL (a proxy in front) is left alone.
    explicit = Config.from_env({**BASE, "MCP_PUBLIC_URL": "https://x.example/mcp"}, port=9000)
    assert explicit.public_url == "https://x.example/mcp"


def test_user_cache_evicts_the_oldest_and_refreshes_in_place():
    cache = UserCache(max_size=2)
    for name in ["a", "b", "a", "c"]:  # refreshing "a" makes "b" the oldest
        cache.put({"username": name})
    assert cache.handles() == ["a", "c"]


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MCP_AUTH_TOKENS": "a-token-of-16-chars=x:read"}, "X_BEARER_TOKEN"),
        ({"X_BEARER_TOKEN": "t"}, "MCP_AUTH_TOKENS is not set"),
        ({**BASE, "MCP_AUTH_TOKENS": "short=x:read"}, "at least 16"),
        ({**BASE, "MCP_AUTH_TOKENS": "a-token-of-16-chars=admin"}, "scopes"),
        ({**BASE, "MCP_PORT": "eighty"}, "MCP_PORT"),
        ({**BASE, "MCP_PUBLIC_URL": "localhost/mcp"}, "MCP_PUBLIC_URL"),
        ({**BASE, "MCP_STATE_KEYS": "too-short"}, "MCP_STATE_KEYS"),
    ],
)
def test_bad_settings_are_refused(env, message):
    with pytest.raises(ConfigError, match=message):
        Config.from_env(env)
