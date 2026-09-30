"""Resources: the stats document and values by URI, with the right error for each failure."""

from __future__ import annotations

import json

import pytest
from mcp.shared.exceptions import MCPError

pytestmark = pytest.mark.anyio


async def test_resources_and_templates_are_listed(client):
    assert [str(r.uri) for r in (await client.list_resources()).resources] == ["cache://stats"]
    templates = (await client.list_resource_templates()).resource_templates
    assert [t.uri_template for t in templates] == ["cache://{namespace}/{key}"]


async def test_value_by_uri(client, redis):
    await redis.set("mcpcache:weather:london", '{"t":14}', ex=60)
    result = await client.read_resource("cache://weather/london")
    assert result.contents[0].text == '{"t":14}'


async def test_missing_value_is_not_found(client):
    with pytest.raises(MCPError) as exc:
        await client.read_resource("cache://weather/paris")
    assert exc.value.error.code == -32602  # ResourceNotFoundError
    assert "No cached value" in exc.value.error.message


async def test_bad_uri_parts_are_a_resource_error_not_not_found(client):
    with pytest.raises(MCPError) as exc:
        await client.read_resource("cache://we*ther/x")
    assert exc.value.error.code == -32603  # ResourceError


async def test_stats_counts_only_this_servers_keys(client, redis):
    await redis.set("mcpcache:a:1", "v")
    await redis.set("mcpcache:b:2", "v")
    await redis.set("someone-else:3", "v")
    await redis.get("mcpcache:a:1")  # a hit
    stats = json.loads((await client.read_resource("cache://stats")).contents[0].text)
    assert stats["owned_keys"] == 2
    assert stats["owned_keys_complete"] is True
    assert stats["key_prefix"] == "mcpcache"
    assert stats["keyspace_hits"] >= 1
