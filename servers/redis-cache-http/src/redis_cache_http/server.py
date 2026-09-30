"""redis-cache-http: a Redis cache MCP server, over Streamable HTTP.

What is different from a stdio server, all in this file:

1. A lifespan that opens one Redis pool for the whole process.   -> `lifespan()`
2. The server object.                                           -> `mcp = MCPServer(...)`
3. Serving over HTTP at a URL, stateless.                       -> `main()`
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.mcpserver import Context, MCPServer
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


# --- 1. Shared state, opened once for the life of the server ------------------------------


@dataclass
class AppContext:
    redis: Redis
    settings: Settings


def make_redis(settings: Settings) -> Redis:
    """Build the Redis client and its connection pool. Tests replace this with a fake."""
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,  # str in, str out; the cache stores text (often JSON)
        max_connections=settings.redis_max_connections,
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
        health_check_interval=30,  # re-check idle connections before reusing them
    )


@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
    # Over HTTP the SDK enters this once when the app starts, not once per request, so every
    # request shares this one pool.
    settings = get_settings()
    redis = make_redis(settings)
    try:
        # Fail fast: refuse to start if Redis is unreachable, instead of failing every call.
        await redis.ping()
    except RedisError as exc:
        await redis.aclose()
        raise RuntimeError(f"Cannot reach Redis at {settings.redis_url}: {exc}") from exc
    logger.info("Connected to Redis; keys are prefixed with %r", settings.cache_key_prefix)
    try:
        yield AppContext(redis=redis, settings=settings)
    finally:
        await redis.aclose()


def app_context(ctx: Context) -> AppContext:
    return ctx.request_context.lifespan_context


# --- 2. The server ------------------------------------------------------------------------

mcp = MCPServer(
    name="redis-cache-http",
    title="Redis cache",
    version="0.1.0",
    instructions=(
        "A shared key-value cache backed by Redis. Store text or JSON under a key with an "
        "expiry (TTL), read it back, and delete it. Keys are grouped by namespace."
    ),
    lifespan=lifespan,
)


# --- 3. Running over Streamable HTTP ------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    logger.info("Serving MCP at http://%s:%d/mcp", settings.mcp_host, settings.mcp_port)
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        # No per-client session state: any request can go to any copy of the server.
        stateless_http=True,
    )
