"""Configuration, read once from the environment and validated before the server starts.

Everything the server needs arrives here, so a bad setting fails at startup with a clear message
instead of on the first request. Tests build a `Config` directly, with no environment at all.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

READ_SCOPE = "x:read"
WRITE_SCOPE = "x:write"
KNOWN_SCOPES = {READ_SCOPE, WRITE_SCOPE}


class ConfigError(ValueError):
    """A setting is missing or malformed. The message names the variable and the fix."""


@dataclass(frozen=True)
class Config:
    x_bearer_token: str
    # Maps each bearer token a client may send to the scopes it grants.
    auth_tokens: Mapping[str, frozenset[str]]
    x_user_access_token: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    public_url: str = "http://127.0.0.1:8000/mcp"
    allowed_hosts: tuple[str, ...] = ()
    state_keys: tuple[str, ...] = field(default=(), repr=False)

    @property
    def can_write(self) -> bool:
        """Posting needs a user-context token; an app-only bearer token can only read."""
        return bool(self.x_user_access_token)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env

        def get(name: str) -> str:
            return env.get(name, "").strip()

        x_bearer_token = get("X_BEARER_TOKEN")
        if not x_bearer_token:
            raise ConfigError("X_BEARER_TOKEN is not set. Create an app at developer.x.com.")

        try:
            port = int(get("MCP_PORT") or 8000)
        except ValueError as exc:
            raise ConfigError("MCP_PORT must be a number.") from exc

        return cls(
            x_bearer_token=x_bearer_token,
            x_user_access_token=get("X_USER_ACCESS_TOKEN") or None,
            auth_tokens=parse_auth_tokens(get("MCP_AUTH_TOKENS")),
            host=get("MCP_HOST") or "127.0.0.1",
            port=port,
            public_url=parse_public_url(get("MCP_PUBLIC_URL") or f"http://127.0.0.1:{port}/mcp"),
            allowed_hosts=split_list(get("MCP_ALLOWED_HOSTS")),
            state_keys=parse_state_keys(get("MCP_STATE_KEYS")),
        )


def split_list(value: str, separator: str = ",") -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(separator) if item.strip())


def parse_auth_tokens(value: str) -> dict[str, frozenset[str]]:
    """Parse `token=scope,scope;token=scope` into {token: scopes}."""
    if not value:
        raise ConfigError(
            "MCP_AUTH_TOKENS is not set. An HTTP server must not run without auth. "
            "Example: MCP_AUTH_TOKENS='<token>=x:read,x:write'."
        )
    tokens: dict[str, frozenset[str]] = {}
    for entry in split_list(value, ";"):
        token, _, scopes_text = entry.partition("=")
        token, scopes = token.strip(), frozenset(split_list(scopes_text))
        if len(token) < 16:
            raise ConfigError("Each MCP_AUTH_TOKENS token must be at least 16 characters.")
        if not scopes or not scopes <= KNOWN_SCOPES:
            raise ConfigError(
                f"MCP_AUTH_TOKENS scopes must be some of {sorted(KNOWN_SCOPES)}, "
                f"got {sorted(scopes)}."
            )
        tokens[token] = scopes
    return tokens


def parse_public_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConfigError(f"MCP_PUBLIC_URL must be an http(s) URL, got {value!r}.")
    return value.rstrip("/")


def parse_state_keys(value: str) -> tuple[str, ...]:
    keys = split_list(value)
    if any(len(key) < 32 for key in keys):
        raise ConfigError(
            "Each MCP_STATE_KEYS key must be at least 32 characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
    return keys
