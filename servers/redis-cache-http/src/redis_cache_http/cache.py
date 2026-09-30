"""The cache itself: key naming and Redis calls, with no MCP in sight.

Keeping this apart from `server.py` means the MCP layer only validates input and shapes
output, and the Redis logic can be read (and tested) on its own.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver.exceptions import ToolError
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# How many SCAN round trips one list call may make, so a sparse namespace in a huge Redis
# cannot turn one tool call into a full walk of the keyspace.
MAX_SCAN_ROUNDS = 10


@asynccontextmanager
async def redis_errors() -> AsyncIterator[None]:
    """Turn Redis failures into a clean ToolError.

    The full error (which can include host names) goes to the server log; the model only sees
    a short message it can act on.
    """
    try:
        yield
    except RedisError as exc:
        logger.warning("Redis call failed: %r", exc)
        raise ToolError("The cache is unavailable right now; try again shortly.") from exc


class RedisCache:
    def __init__(self, redis: Redis, prefix: str) -> None:
        self.redis = redis
        self.prefix = prefix

    def _full_key(self, namespace: str, key: str) -> str:
        # e.g. "mcpcache:weather:london". Namespaces cannot contain ":", so the split is exact.
        return f"{self.prefix}:{namespace}:{key}"

    async def get(self, namespace: str, key: str) -> tuple[str | None, int | None]:
        full = self._full_key(namespace, key)
        # One round trip for both: a pipeline sends the commands together.
        async with redis_errors(), self.redis.pipeline(transaction=False) as pipe:
            value, ttl = await pipe.get(full).ttl(full).execute()
        if value is None:
            return None, None
        return value, ttl if ttl >= 0 else None

    async def set(self, namespace: str, key: str, value: str, ttl_seconds: int) -> bool:
        """Store the value with an expiry. Returns True if it replaced an existing value."""
        async with redis_errors():
            # GET=True returns the old value in the same atomic command.
            old = await self.redis.set(
                self._full_key(namespace, key), value, ex=ttl_seconds, get=True
            )
        return old is not None

    async def delete(self, namespace: str, key: str) -> bool:
        async with redis_errors():
            # UNLINK frees memory in the background, so a big value never stalls Redis.
            return await self.redis.unlink(self._full_key(namespace, key)) > 0

    async def list_keys(
        self, namespace: str, pattern: str, cursor: int, limit: int
    ) -> tuple[list[str], int]:
        """One page of keys via SCAN. Returns (keys, next_cursor); next_cursor 0 means done.

        SCAN may return fewer or more keys than asked for per round, so a page can hold
        slightly more than `limit` keys. That keeps the cursor exact: nothing is skipped.
        """
        base = f"{self.prefix}:{namespace}:"
        keys: list[str] = []
        async with redis_errors():
            for _ in range(MAX_SCAN_ROUNDS):
                cursor, batch = await self.redis.scan(cursor, match=base + pattern, count=limit)
                keys.extend(k.removeprefix(base) for k in batch)
                if cursor == 0 or len(keys) >= limit:
                    break
        return sorted(keys), cursor
