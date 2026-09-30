"""Shared fixtures: settings, a Redis (fake by default), an in-process client, the HTTP app.

Tests run against `fakeredis`, an in-memory Redis, so they need no network. Set
REDIS_TEST_URL (e.g. redis://localhost:6379/15) to run the same tests against a real Redis;
that database is flushed before each test, so never point it at data you care about.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx2
import pytest
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from redis.asyncio import Redis

from redis_cache_http import server
from redis_cache_http.config import Settings

TOKEN = "test-token-0123456789-0123456789-abcdef"
BASE_URL = "http://127.0.0.1:8000"
MCP_URL = f"{BASE_URL}/mcp"
REAL_REDIS_URL = os.environ.get("REDIS_TEST_URL")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_settings(**overrides: object) -> Settings:
    # _env_file=None: tests never read a developer's .env.
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def token() -> str:
    return TOKEN


@pytest.fixture
def settings() -> Settings:
    return make_settings(cache_default_ttl_seconds=3600, cache_max_ttl_seconds=86400)


@pytest.fixture
def fake_server() -> FakeServer:
    return FakeServer()


# fakeredis does not implement INFO (found by comparing it with a real Redis 7, see the server
# CLAUDE.md). This is the subset cache://stats reads, with the value types real Redis returns.
FAKE_INFO = {
    "redis_version": "7.0.15",
    "used_memory_human": "1.00M",
    "keyspace_hits": 1,
    "keyspace_misses": 0,
}


@pytest.fixture
def redis_factory(monkeypatch, fake_server):
    """Replace server.make_redis, so every server in a test shares one (fake or real) Redis."""

    def make(settings: Settings) -> Redis:
        if REAL_REDIS_URL:
            return Redis.from_url(REAL_REDIS_URL, decode_responses=True)
        fake = FakeRedis(server=fake_server, decode_responses=True)

        async def info(*_sections: str) -> dict[str, object]:
            return dict(FAKE_INFO)

        fake.info = info  # type: ignore[method-assign]
        return fake

    monkeypatch.setattr(server, "make_redis", make)
    return make


@pytest.fixture
async def redis(redis_factory, settings) -> AsyncIterator[Redis]:
    """Direct access to the same Redis the server uses, to arrange and inspect data."""
    r = redis_factory(settings)
    await r.flushdb()
    yield r
    await r.aclose()


@pytest.fixture
async def client(settings, redis) -> AsyncIterator[Client]:
    """In-process: no HTTP and no auth, just the MCP protocol against the server object."""
    async with Client(server.create_server(settings)) as c:
        yield c


@pytest.fixture
def auth_settings() -> Settings:
    return make_settings(mcp_auth_token=TOKEN)


@pytest.fixture
def app(auth_settings, redis):
    return server.create_app(auth_settings)


@pytest.fixture
async def http(app) -> AsyncIterator[httpx2.AsyncClient]:
    """Plain HTTP against the real ASGI app (routing, auth, Host checks), in-process."""
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as c:
            yield c


@pytest.fixture
async def http_client(app) -> AsyncIterator[Client]:
    """A real MCP client over Streamable HTTP, with the token, to the in-process app."""
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with (
            httpx2.AsyncClient(transport=transport, base_url=BASE_URL, headers=headers) as h,
            Client(streamable_http_client(MCP_URL, http_client=h)) as c,
        ):
            yield c
