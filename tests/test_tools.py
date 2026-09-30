"""Tools, through the MCP protocol: schemas, annotations, results and error mapping."""

from __future__ import annotations

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

pytestmark = pytest.mark.anyio


async def call(client, name: str, **args):
    return await client.call_tool(name, args)


def text(result) -> str:
    return "\n".join(block.text for block in result.content)


async def test_tools_are_listed_with_honest_annotations_and_output_schemas(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"cache_get", "cache_set", "cache_delete", "cache_list_keys"}
    for name in ("cache_get", "cache_list_keys"):
        assert tools[name].annotations.read_only_hint is True
    for name in ("cache_set", "cache_delete"):
        assert tools[name].annotations.read_only_hint is False
        assert tools[name].annotations.destructive_hint is True
    assert all(t.output_schema for t in tools.values())


async def test_miss_is_a_normal_answer_not_an_error(client):
    result = await call(client, "cache_get", namespace="weather", key="london")
    assert not result.is_error
    assert result.structured_content["found"] is False


async def test_set_then_get_round_trip_with_ttl(client, redis):
    result = await call(
        client, "cache_set", namespace="weather", key="london", value='{"t":14}', ttl_seconds=120
    )
    assert result.structured_content == {
        "namespace": "weather",
        "key": "london",
        "ttl_seconds": 120,
        "replaced": False,
    }
    got = (await call(client, "cache_get", namespace="weather", key="london")).structured_content
    assert got["found"] is True
    assert got["value"] == '{"t":14}'
    assert 0 < got["ttl_seconds"] <= 120
    # The real key is prefixed and namespaced, and it expires.
    assert await redis.ttl("mcpcache:weather:london") > 0


async def test_set_again_replaces_and_uses_default_ttl(client):
    await call(client, "cache_set", namespace="n", key="k", value="a")
    result = await call(client, "cache_set", namespace="n", key="k", value="b")
    assert result.structured_content["replaced"] is True
    assert result.structured_content["ttl_seconds"] == 3600


async def test_ttl_over_the_limit_is_a_tool_error(client, redis):
    result = await call(client, "cache_set", namespace="n", key="k", value="v", ttl_seconds=10**9)
    assert result.is_error
    assert "at most 86400" in text(result)
    assert await redis.exists("mcpcache:n:k") == 0


@pytest.mark.parametrize(
    "args",
    [
        {"namespace": "a:b", "key": "k"},  # ":" would escape the namespace
        {"namespace": "n", "key": "*"},  # a key must never act as a wildcard
        {"namespace": "", "key": "k"},
    ],
)
async def test_bad_names_are_rejected_by_the_schema(client, args):
    result = await client.call_tool("cache_get", args)
    assert result.is_error
    assert "pattern" in text(result) or "at least" in text(result)


async def test_delete_reports_whether_anything_was_there(client):
    await call(client, "cache_set", namespace="n", key="k", value="v")
    first = await call(client, "cache_delete", namespace="n", key="k")
    second = await call(client, "cache_delete", namespace="n", key="k")
    assert first.structured_content["deleted"] is True
    assert second.structured_content["deleted"] is False


async def test_list_keys_pages_through_everything_once(client, redis):
    for i in range(25):
        await redis.set(f"mcpcache:ns:item:{i}", "v")
    await redis.set("mcpcache:ns:other", "v")
    await redis.set("mcpcache:elsewhere:item:1", "v")  # another namespace
    await redis.set("unrelated:item:1", "v")  # another app's key

    seen: list[str] = []
    cursor = None
    for _ in range(50):
        args = {"namespace": "ns", "pattern": "item:*", "limit": 5}
        if cursor:
            args["cursor"] = cursor
        page = (await client.call_tool("cache_list_keys", args)).structured_content
        seen += page["keys"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(f"item:{i}" for i in range(25))
    assert len(seen) == len(set(seen))


async def test_redis_failure_is_a_clean_error_without_details(client, monkeypatch):
    async def broken(*_args, **_kwargs):
        raise RedisConnectionError("Error 111 connecting to secret-host:6379")

    # FakeRedis subclasses the real client, so this breaks both.
    monkeypatch.setattr(Redis, "unlink", broken)
    result = await call(client, "cache_delete", namespace="n", key="k")
    assert result.is_error
    assert "unavailable" in text(result)
    assert "secret-host" not in text(result)
