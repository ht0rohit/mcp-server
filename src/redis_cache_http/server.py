"""redis-cache-http: a Redis cache MCP server, over Streamable HTTP.

What is different from a stdio server, all in this file:

1. One Redis pool for the whole process, opened in the lifespan.  -> `create_server()`
2. Tools with typed input and output.                             -> `cache_get()`, ...
3. Resources: data a client can read by URI.                      -> `cached_value()`, ...
4. A plain HTTP health route next to the MCP endpoint.            -> `create_server()`
5. The server, built by a factory from settings (auth included).  -> `create_server()`
6. The ASGI app with DNS-rebinding protection, and running it.    -> `create_app()`, `main()`
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import AnyHttpUrl, Field, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import StaticTokenVerifier
from .cache import RedisCache
from .config import Settings, get_settings
from .models import CacheEntry, CacheStats, DeleteResult, KeyPage, SetResult

logger = logging.getLogger(__name__)

NAME = "redis-cache-http"
VERSION = "0.1.0"
INSTRUCTIONS = (
    "A shared key-value cache backed by Redis. Store text or JSON under a key with an "
    "expiry (TTL), read it back, and delete it. Keys are grouped by namespace."
)


# --- 1. Shared state ----------------------------------------------------------------------


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


# --- 2. Tools -----------------------------------------------------------------------------
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


async def cache_get(namespace: Namespace, key: Key, ctx: Context) -> CacheEntry:
    """Read a cached value. Returns found=false on a miss (never stored, or expired).

    Check the cache with this before doing slow or expensive work, and store the result
    with cache_set afterwards.
    """
    value, ttl = await cache(ctx).get(namespace, key)
    return CacheEntry(
        namespace=namespace, key=key, found=value is not None, value=value, ttl_seconds=ttl
    )


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


async def cache_delete(namespace: Namespace, key: Key, ctx: Context) -> DeleteResult:
    """Delete one cached value, e.g. when the data it came from has changed."""
    deleted = await cache(ctx).delete(namespace, key)
    return DeleteResult(namespace=namespace, key=key, deleted=deleted)


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


TOOLS = [
    (cache_get, "Get cached value", READ_ONLY),
    (cache_set, "Store value in cache", OVERWRITE),
    (cache_delete, "Delete cached value", DELETE),
    (cache_list_keys, "List cached keys", READ_ONLY),
]


# --- 3. Resources -------------------------------------------------------------------------
#
# Tools are actions the model chooses to call; resources are data the user or app attaches as
# context, like opening a file. Resources only read. Failures use resource errors:
# ResourceNotFoundError when the URI names nothing, ResourceError for everything else.

NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


async def cached_value(namespace: str, key: str, ctx: Context) -> str:
    # URI parts skip the tool argument schemas, so validate them here the same way.
    if not NAMESPACE_RE.match(namespace) or not KEY_RE.match(key):
        raise ResourceError("Namespace may use letters, digits, _ and -; key also . and :")
    value, _ttl = await cache(ctx).get(namespace, key)
    if value is None:
        raise ResourceNotFoundError(f"No cached value at cache://{namespace}/{key}")
    return value


# --- 4 and 5. The server (with auth) and its health route -------------------------------
#
# A factory instead of a module-level `mcp`: auth is part of the server's constructor and
# depends on settings, and tests can build a server with their own settings and fake Redis.


def create_server(settings: Settings) -> MCPServer:
    # Two handlers get no `ctx`: the health route (plain HTTP, outside MCP) and static
    # resources (the SDK only injects Context into templated ones). The lifespan publishes the
    # running cache here for them; everything else reads it from `ctx`.
    running: dict[str, RedisCache] = {}

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
        # Over HTTP the SDK enters this once when the app starts, not once per request, so
        # every request shares this one pool.
        redis = make_redis(settings)
        try:
            # Fail fast: refuse to start if Redis is unreachable, instead of failing every call.
            await redis.ping()
        except RedisError as exc:
            await redis.aclose()
            raise RuntimeError(f"Cannot reach Redis: {exc}") from exc
        logger.info("Connected to Redis; keys are prefixed with %r", settings.cache_key_prefix)
        running["cache"] = RedisCache(redis, settings.cache_key_prefix)
        try:
            yield AppContext(cache=running["cache"], settings=settings)
        finally:
            running.clear()
            await redis.aclose()

    auth: dict[str, object] = {}
    if settings.mcp_auth_token is not None:
        # The resource is the MCP endpoint's own URL, so its metadata is served at
        # /.well-known/oauth-protected-resource/mcp, where MCP clients look first.
        resource = f"{settings.public_url}/mcp"
        auth = {
            "token_verifier": StaticTokenVerifier(
                settings.mcp_auth_token.get_secret_value(), resource=resource
            ),
            # The protected-resource metadata must name an authorization server, so it names
            # this server. Nothing here issues tokens: clients send a static
            # `Authorization: Bearer ...` header. Real OAuth replaces this in server 04.
            "auth": AuthSettings(
                issuer_url=AnyHttpUrl(settings.public_url),
                resource_server_url=AnyHttpUrl(resource),
                validate_token_resource=True,
            ),
        }

    mcp = MCPServer(
        name=NAME,
        title="Redis cache",
        version=VERSION,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        **auth,
    )

    for fn, title, hints in TOOLS:
        mcp.add_tool(fn, title=title, annotations=hints)

    @mcp.resource(
        "cache://stats",
        title="Cache stats",
        description="Redis version, memory, hit rate, and how many keys this server owns",
        mime_type="application/json",
    )
    async def cache_stats() -> str:
        if "cache" not in running:
            raise ResourceError("The server is not connected to Redis.")
        return CacheStats.model_validate(await running["cache"].stats()).model_dump_json(indent=2)

    mcp.resource(
        "cache://{namespace}/{key}",
        title="Cached value",
        description="The value stored under namespace/key, as stored (often JSON)",
        mime_type="text/plain",
    )(cached_value)

    # Health check: plain HTTP for Docker, Kubernetes or a load balancer. 200 when Redis
    # answers, 503 when not. Custom routes skip auth (orchestrators send no token), so it
    # reveals nothing beyond ok / not ok.
    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_request: Request) -> JSONResponse:
        if "cache" in running and await running["cache"].ping():
            return JSONResponse({"status": "ok"})
        return JSONResponse({"status": "unavailable"}, status_code=503)

    return mcp


# --- 6. The ASGI app and running it -------------------------------------------------------


def create_app(settings: Settings | None = None) -> Starlette:
    """MCP at /mcp, /healthz, and (with a token) /.well-known/oauth-protected-resource.

    `uvicorn --factory redis_cache_http.server:create_app` serves it too, which is how you
    would run several worker processes.
    """
    settings = settings or get_settings()
    security = TransportSecuritySettings(
        # DNS-rebinding protection: a web page in your browser can make it send requests to
        # localhost under the page's own host name. Accept only Host and Origin values we know.
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )
    return create_server(settings).streamable_http_app(
        # No per-client session state: any request can go to any copy of the server.
        stateless_http=True,
        transport_security=security,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = get_settings()
    except ValidationError as exc:
        # One line per problem, without pydantic's URLs, and never the input values.
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'settings'}: {e['msg']}"
            for e in exc.errors(include_url=False, include_input=False)
        )
        print(f"{NAME}: invalid configuration: {problems}", file=sys.stderr)
        sys.exit(1)

    import uvicorn

    logger.info(
        "Serving MCP at http://%s:%d/mcp (auth %s)",
        settings.mcp_host,
        settings.mcp_port,
        "on" if settings.mcp_auth_token else "off, loopback only",
    )
    uvicorn.run(create_app(settings), host=settings.mcp_host, port=settings.mcp_port)
