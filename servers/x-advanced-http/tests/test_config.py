"""Configuration fails fast, naming the variable to fix."""

from __future__ import annotations

import pytest
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
