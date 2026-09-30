"""Typed settings, read once from environment variables (and an optional .env file).

Every field here is listed in `.env.example`. A missing or malformed value fails at startup
with a clear pydantic error instead of deep inside a request.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Where Redis lives. rediss:// (two s) for TLS; credentials go in the URL.
    redis_url: str = "redis://localhost:6379/0"
    # The pool is shared by every request; this caps how many sockets it opens.
    redis_max_connections: int = Field(default=20, ge=1, le=1000)
    # Seconds to wait for Redis before giving up, so a dead Redis fails fast.
    redis_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # Loopback by default: only this machine can reach the server until auth is added.
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8000, ge=1, le=65535)

    # Bearer token clients must send. SecretStr keeps it out of reprs, logs and tracebacks.
    # Optional on loopback; required as soon as the server listens on anything else.
    mcp_auth_token: SecretStr | None = Field(default=None, min_length=32)
    # The URL clients use to reach /mcp's host, e.g. https://cache.example.com. It names this
    # server in the auth metadata and is allowed as a Host. Defaults to the loopback address.
    mcp_public_url: AnyHttpUrl | None = None
    # Extra Host header values to accept (comma-separated), e.g. behind a proxy.
    mcp_allowed_hosts: str = ""

    # Every key the server touches starts with this, so it never reads or deletes keys that
    # other apps keep in the same Redis.
    cache_key_prefix: str = Field(default="mcpcache", pattern=r"^[A-Za-z0-9_-]+$")
    # TTL used when a tool call does not give one, and the most a call may ask for.
    cache_default_ttl_seconds: int = Field(default=3600, ge=1)
    cache_max_ttl_seconds: int = Field(default=7 * 24 * 3600, ge=1)

    @model_validator(mode="after")
    def _secure_when_exposed(self) -> Self:
        if self.mcp_host not in LOOPBACK_HOSTS:
            if self.mcp_auth_token is None:
                raise ValueError(
                    f"MCP_HOST={self.mcp_host} exposes the server beyond this machine: "
                    "set MCP_AUTH_TOKEN (32+ characters) first."
                )
            if self.mcp_public_url is None:
                raise ValueError(
                    f"MCP_HOST={self.mcp_host}: set MCP_PUBLIC_URL to the URL clients use."
                )
        return self

    @property
    def public_url(self) -> str:
        """Base URL of this server, without a trailing slash. Follows MCP_PORT by default."""
        if self.mcp_public_url is not None:
            return str(self.mcp_public_url).rstrip("/")
        return f"http://127.0.0.1:{self.mcp_port}"

    @property
    def allowed_hosts(self) -> list[str]:
        """Host header values to accept: loopback on our port, the public URL, and extras."""
        port = self.mcp_port
        hosts = [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]
        hosts.append(urlsplit(self.public_url).netloc)
        hosts += [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]
        return list(dict.fromkeys(hosts))

    @property
    def allowed_origins(self) -> list[str]:
        """Browser origins to accept: loopback on our port and the public URL."""
        port = self.mcp_port
        origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}", self.public_url]
        return list(dict.fromkeys(origins))


@lru_cache
def get_settings() -> Settings:
    return Settings()
