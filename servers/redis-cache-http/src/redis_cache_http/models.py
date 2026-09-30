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
