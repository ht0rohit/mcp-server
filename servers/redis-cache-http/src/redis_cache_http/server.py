"""redis-cache-http: a Redis cache MCP server, over Streamable HTTP.

What is different from a stdio server, all in this file:

1. A lifespan that opens one Redis pool for the whole process.   -> `lifespan()`
2. The server object.                                           -> `mcp = MCPServer(...)`
3. Tools with typed input and output.                           -> `@mcp.tool(...)`
4. Resources: data a client can read by URI.                    -> `@mcp.resource(...)`
5. A plain HTTP health route next to the MCP endpoint.          -> `@mcp.custom_route(...)`
6. Serving over HTTP at a URL, stateless.                       -> `main()`
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.requests import Request
from starlette.responses import JSONResponse

from .cache import RedisCache
from .config import Settings, get_settings
from .models import CacheEntry, CacheStats, DeleteResult, KeyPage, SetResult

logger = logging.getLogger(__name__)


# --- 1. Shared state, opened once for the life of the server ------------------------------


@dataclass
class AppContext:
    cache: RedisCache
    settings: Settings


# Two handlers get no `ctx`: the health route (plain HTTP, outside MCP) and static resources
# (the SDK only injects Context into templated ones). The lifespan publishes the running cache
# here for them; everything else reads it from `ctx`.
_running: dict[str, RedisCache] = {}


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
    cache = RedisCache(redis, settings.cache_key_prefix)
    _running["cache"] = cache
    try:
        yield AppContext(cache=cache, settings=settings)
    finally:
        _running.clear()
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
# - A cache miss is a normal answer (`found: false`), not an error. Errors are for real
#   failures: `ToolError` for input only the server can judge (a TTL over the limit), and
#   `CacheUnavailableError` (see cache.py) when Redis is unreachable.
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


# --- 4. Resources -------------------------------------------------------------------------
#
# Tools are actions the model chooses to call; resources are data the user or app attaches as
# context, like opening a file. Resources only read. Failures use resource errors:
# ResourceNotFoundError when the URI names nothing, ResourceError for everything else.

NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


@mcp.resource(
    "cache://stats",
    title="Cache stats",
    description="Redis version, memory, hit rate, and how many keys this server owns",
    mime_type="application/json",
)
async def cache_stats() -> str:
    running = _running.get("cache")
    if running is None:
        raise ResourceError("The server is not connected to Redis.")
    return CacheStats.model_validate(await running.stats()).model_dump_json(indent=2)


@mcp.resource(
    "cache://{namespace}/{key}",
    title="Cached value",
    description="The value stored under namespace/key, as stored (often JSON)",
    mime_type="text/plain",
)
async def cached_value(namespace: str, key: str, ctx: Context) -> str:
    # URI parts skip the tool argument schemas, so validate them here the same way.
    if not NAMESPACE_RE.match(namespace) or not KEY_RE.match(key):
        raise ResourceError("Namespace may use letters, digits, _ and -; key also . and :")
    value, _ttl = await cache(ctx).get(namespace, key)
    if value is None:
        raise ResourceNotFoundError(f"No cached value at cache://{namespace}/{key}")
    return value


# --- 5. Health check ----------------------------------------------------------------------
#
# Plain HTTP for Docker, Kubernetes or a load balancer: 200 when Redis answers, 503 when not.
# It stays open when auth is added (orchestrators do not send tokens), so it reveals nothing
# beyond ok / not ok.


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(_request: Request) -> JSONResponse:
    running = _running.get("cache")
    if running is not None and await running.ping():
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "unavailable"}, status_code=503)


# --- 6. Running over Streamable HTTP ------------------------------------------------------


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
