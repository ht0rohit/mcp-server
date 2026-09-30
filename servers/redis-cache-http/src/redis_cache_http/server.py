"""redis-cache-http: a Redis cache MCP server, over Streamable HTTP.

What is different from a stdio server, all in this file:

1. A lifespan that opens one Redis pool for the whole process.   -> `lifespan()`
2. The server object.                                           -> `mcp = MCPServer(...)`
3. Tools with typed input and output.                           -> `@mcp.tool(...)`
4. Serving over HTTP at a URL, stateless.                       -> `main()`
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .cache import RedisCache
from .config import Settings, get_settings
from .models import CacheEntry, DeleteResult, KeyPage, SetResult

logger = logging.getLogger(__name__)


# --- 1. Shared state, opened once for the life of the server ------------------------------


@dataclass
class AppContext:
    cache: RedisCache
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
        yield AppContext(cache=RedisCache(redis, settings.cache_key_prefix), settings=settings)
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


# --- 3. Tools -----------------------------------------------------------------------------
#
# Best practices shown below:
# - Prefixed, verb-first names (`cache_get`) so they never clash with other servers' tools.
# - The docstring is what the model reads: say what it does, what it returns, when to use it.
# - Flat, annotated arguments: the limits (`pattern`, `ge`, `max_length`) land in the input
#   schema, and the SDK rejects bad input before our code runs.
# - Annotations are honest hints for the client (and the human approving calls).
# - A cache miss is a normal answer (`found: false`), not an error. `ToolError` is for real
#   failures: Redis unreachable, or input only the server can judge (a TTL over the limit).
# - Typed return models give every tool an `outputSchema` and structured JSON results.

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
# Overwrites whatever was there (destructive), but repeating the same call changes nothing more.
OVERWRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
DELETE = OVERWRITE

MAX_VALUE_CHARS = 100_000

Namespace = Annotated[
    str,
    Field(
        description="Group of related keys, e.g. 'weather' or 'user-42'. Letters, digits, _ and -",
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
    ),
]
# No glob characters in keys, so a key can never act as a wildcard.
Key = Annotated[
    str,
    Field(
        description="Key within the namespace, e.g. 'london' or 'report:2026-09'",
        pattern=r"^[A-Za-z0-9_.:-]{1,200}$",
    ),
]


def cache(ctx: Context) -> RedisCache:
    return ctx.request_context.lifespan_context.cache


def settings(ctx: Context) -> Settings:
    return ctx.request_context.lifespan_context.settings


@mcp.tool(title="Get cached value", annotations=READ_ONLY)
async def cache_get(namespace: Namespace, key: Key, ctx: Context) -> CacheEntry:
    """Read a cached value. Returns found=false on a miss (never stored, or expired).

    Check the cache with this before doing slow or expensive work, and store the result
    with cache_set afterwards.
    """
    value, ttl = await cache(ctx).get(namespace, key)
    return CacheEntry(
        namespace=namespace, key=key, found=value is not None, value=value, ttl_seconds=ttl
    )


@mcp.tool(title="Store value in cache", annotations=OVERWRITE)
async def cache_set(
    namespace: Namespace,
    key: Key,
    value: Annotated[
        str,
        Field(max_length=MAX_VALUE_CHARS, description="Text to store; serialize objects as JSON"),
    ],
    ctx: Context,
    ttl_seconds: Annotated[
        int | None,
        Field(ge=1, description="Seconds until it expires. Omit for the server default (1 hour)"),
    ] = None,
) -> SetResult:
    """Store a value under namespace/key, replacing any existing value. Every value expires."""
    limits = settings(ctx)
    ttl = ttl_seconds or limits.cache_default_ttl_seconds
    if ttl > limits.cache_max_ttl_seconds:
        raise ToolError(f"ttl_seconds can be at most {limits.cache_max_ttl_seconds}.")
    replaced = await cache(ctx).set(namespace, key, value, ttl)
    return SetResult(namespace=namespace, key=key, ttl_seconds=ttl, replaced=replaced)


@mcp.tool(title="Delete cached value", annotations=DELETE)
async def cache_delete(namespace: Namespace, key: Key, ctx: Context) -> DeleteResult:
    """Delete one cached value, e.g. when the data it came from has changed."""
    deleted = await cache(ctx).delete(namespace, key)
    return DeleteResult(namespace=namespace, key=key, deleted=deleted)


@mcp.tool(title="List cached keys", annotations=READ_ONLY)
async def cache_list_keys(
    namespace: Namespace,
    ctx: Context,
    pattern: Annotated[
        str,
        Field(
            description="Glob on the key: * matches any run, ? one character, e.g. 'report:*'",
            pattern=r"^[A-Za-z0-9_.:*?-]{1,200}$",
        ),
    ] = "*",
    limit: Annotated[int, Field(ge=1, le=500, description="Keys per page (about)")] = 100,
    cursor: Annotated[
        str | None, Field(description="next_cursor from a previous call", pattern=r"^\d+$")
    ] = None,
) -> KeyPage:
    """List keys in a namespace, a page at a time. Keys only; use cache_get for values."""
    keys, next_cursor = await cache(ctx).list_keys(namespace, pattern, int(cursor or 0), limit)
    return KeyPage(
        namespace=namespace, keys=keys, next_cursor=str(next_cursor) if next_cursor else None
    )


# --- 4. Running over Streamable HTTP ------------------------------------------------------


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
