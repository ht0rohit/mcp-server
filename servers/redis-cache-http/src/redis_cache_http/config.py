"""Typed settings, read once from environment variables (and an optional .env file).

Every field here is listed in `.env.example`. A missing or malformed value fails at startup
with a clear pydantic error instead of deep inside a request.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Every key the server touches starts with this, so it never reads or deletes keys that
    # other apps keep in the same Redis.
    cache_key_prefix: str = Field(default="mcpcache", pattern=r"^[A-Za-z0-9_-]+$")
    # TTL used when a tool call does not give one, and the most a call may ask for.
    cache_default_ttl_seconds: int = Field(default=3600, ge=1)
    cache_max_ttl_seconds: int = Field(default=7 * 24 * 3600, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
