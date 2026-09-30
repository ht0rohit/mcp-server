"""Typed tool results.

Each tool returns one of these models. The return annotation becomes the tool's
`outputSchema`, so clients get a JSON object with a known shape (`structured_content`) plus the
same JSON as text for the model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CacheEntry(BaseModel):
    namespace: str
    key: str
    found: bool = Field(description="False on a cache miss (the key is absent or expired)")
    value: str | None = Field(default=None, description="The stored value, when found")
    ttl_seconds: int | None = Field(
        default=None, description="Seconds until the value expires, when found"
    )


class SetResult(BaseModel):
    namespace: str
    key: str
    ttl_seconds: int = Field(description="Seconds until the value expires")
    replaced: bool = Field(description="True if an existing value was overwritten")


class DeleteResult(BaseModel):
    namespace: str
    key: str
    deleted: bool = Field(description="False if there was nothing to delete")


class KeyPage(BaseModel):
    namespace: str
    keys: list[str] = Field(description="Keys in this page, without the namespace prefix")
    next_cursor: str | None = Field(
        default=None, description="Pass back as `cursor` for the next page; null when done"
    )


class CacheStats(BaseModel):
    redis_version: str | None
    used_memory_human: str | None = Field(description="Memory Redis uses in total, e.g. '1.2M'")
    keyspace_hits: int = Field(description="Server-wide successful lookups since Redis started")
    keyspace_misses: int = Field(description="Server-wide failed lookups since Redis started")
    hit_rate: float | None = Field(description="hits / (hits + misses); null before any lookup")
    key_prefix: str
    owned_keys: int = Field(description="Keys under this server's prefix")
    owned_keys_complete: bool = Field(
        description="False if counting stopped early; owned_keys is then a lower bound"
    )
