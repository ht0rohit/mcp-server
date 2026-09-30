"""Settings: secure defaults, and values derived from other values."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from redis_cache_http.config import Settings, get_settings
from redis_cache_http.server import main

TOKEN = "config-token-0123456789-0123456789-abc"


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_loopback_without_a_token_is_allowed():
    settings = make_settings()
    assert settings.mcp_auth_token is None
    assert settings.public_url == "http://127.0.0.1:8000"


def test_exposed_host_needs_a_token():
    with pytest.raises(ValidationError, match="MCP_AUTH_TOKEN"):
        make_settings(mcp_host="0.0.0.0")


def test_exposed_host_needs_a_public_url():
    with pytest.raises(ValidationError, match="MCP_PUBLIC_URL"):
        make_settings(mcp_host="0.0.0.0", mcp_auth_token=TOKEN)


def test_bad_config_is_reported_without_echoing_the_token(monkeypatch, capsys):
    # pydantic's own message includes the input value, so main() must not print it as is.
    monkeypatch.setenv("MCP_AUTH_TOKEN", "short-secret")
    monkeypatch.chdir("/")  # no .env file here
    get_settings.cache_clear()
    with pytest.raises(SystemExit):
        main()
    get_settings.cache_clear()
    err = capsys.readouterr().err
    assert "mcp_auth_token" in err
    assert "short-secret" not in err


def test_public_url_and_allowed_hosts_follow_the_port():
    settings = make_settings(mcp_port=8123)
    assert settings.public_url == "http://127.0.0.1:8123"
    assert "localhost:8123" in settings.allowed_hosts
    assert "http://127.0.0.1:8123" in settings.allowed_origins


def test_public_url_host_is_allowed_with_extras():
    settings = make_settings(
        mcp_host="0.0.0.0",
        mcp_auth_token=TOKEN,
        mcp_public_url="https://cache.example.com/",
        mcp_allowed_hosts="proxy.internal:8080, other.example",
    )
    assert settings.public_url == "https://cache.example.com"
    assert {"cache.example.com", "proxy.internal:8080", "other.example"} <= set(
        settings.allowed_hosts
    )
