"""Tests through the MCP protocol, in-process: list, call, read, get, like a real client would."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.anyio


def text(result) -> str:
    return "\n".join(block.text for block in result.content)


TOOLS = {
    "x_get_user",
    "x_get_users",
    "x_get_post",
    "x_get_posts",
    "x_get_user_posts",
    "x_get_user_mentions",
    "x_search_recent_posts",
}


async def test_lists_tools_with_annotations_and_schemas(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == TOOLS
    for tool in tools.values():
        assert tool.description
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True
        assert tool.output_schema is None  # plain-text tools
    size = tools["x_search_recent_posts"].input_schema["properties"]["max_results"]
    assert (size["minimum"], size["maximum"]) == (10, 100)
    assert tools["x_get_user"].input_schema["required"] == ["username"]


async def test_server_sends_instructions(client):
    assert "x://guides/search-operators" in client.instructions


async def test_get_user_accepts_at_sign(client):
    result = await client.call_tool("x_get_user", {"username": "@XDevelopers"})
    assert not result.is_error
    assert "@XDevelopers (Developers), id 2244994945 [verified]" in text(result)


async def test_unknown_user_is_a_tool_error(client):
    result = await client.call_tool("x_get_user", {"username": "nobody_here"})
    assert result.is_error
    assert "No X user named @nobody_here" in text(result)


async def test_invalid_username_is_rejected_before_any_request(client, requests):
    result = await client.call_tool("x_get_user", {"username": "not a handle!"})
    assert result.is_error
    assert requests == []


async def test_get_users_reports_missing(client):
    result = await client.call_tool("x_get_users", {"usernames": ["XDevelopers", "ghost"]})
    assert not result.is_error
    assert "@XDevelopers" in text(result)
    assert "Not found: ghost" in text(result)


async def test_get_post_and_posts(client):
    one = await client.call_tool("x_get_post", {"post_id": "1460323737035677698"})
    assert "@XDevelopers" in text(one) and "likes 10" in text(one)

    many = await client.call_tool("x_get_posts", {"post_ids": ["1460323737035677698", "1"]})
    assert "new era" in text(many) and "Not found: 1" in text(many)

    missing = await client.call_tool("x_get_post", {"post_id": "1"})
    assert missing.is_error


async def test_user_posts_paginate_and_exclude_by_default(client, requests):
    first = await client.call_tool("x_get_user_posts", {"username": "XDevelopers"})
    assert "cursor='page2'" in text(first)
    timeline = requests[-1]
    assert timeline.url.path == "/2/users/2244994945/tweets"
    assert timeline.url.params["exclude"] == "replies,retweets"

    second = await client.call_tool(
        "x_get_user_posts", {"username": "XDevelopers", "cursor": "page2", "include_replies": True}
    )
    assert "cursor=" not in text(second)
    assert requests[-1].url.params["pagination_token"] == "page2"
    assert requests[-1].url.params["exclude"] == "retweets"


async def test_mentions(client, requests):
    result = await client.call_tool("x_get_user_mentions", {"username": "XDevelopers"})
    assert not result.is_error
    assert requests[-1].url.path == "/2/users/2244994945/mentions"


async def test_search_passes_parameters(client, requests):
    result = await client.call_tool(
        "x_search_recent_posts",
        {"query": "mcp lang:en", "max_results": 25, "sort_order": "relevancy"},
    )
    assert "new era" in text(result)
    params = requests[-1].url.params
    assert (params["query"], params["max_results"], params["sort_order"]) == (
        "mcp lang:en",
        "25",
        "relevancy",
    )


async def test_search_errors_are_explained(client):
    empty = await client.call_tool("x_search_recent_posts", {"query": "nothing"})
    assert not empty.is_error and "No posts" in text(empty)

    limited = await client.call_tool("x_search_recent_posts", {"query": "rate-limited"})
    assert limited.is_error and "rate limit" in text(limited)

    forbidden = await client.call_tool("x_search_recent_posts", {"query": "forbidden"})
    assert forbidden.is_error and "plan" in text(forbidden)


async def test_search_rejects_out_of_range_page_size(client, requests):
    result = await client.call_tool("x_search_recent_posts", {"query": "mcp", "max_results": 5})
    assert result.is_error
    assert requests == []


async def test_resources(client):
    static = [r.uri for r in (await client.list_resources()).resources]
    assert "x://guides/search-operators" in static
    templates = [
        t.uri_template for t in (await client.list_resource_templates()).resource_templates
    ]
    assert templates == ["x://users/{username}"]

    guide = await client.read_resource("x://guides/search-operators")
    assert "from:" in guide.contents[0].text

    profile = await client.read_resource("x://users/XDevelopers")
    assert json.loads(profile.contents[0].text)["id"] == "2244994945"


async def test_prompts(client):
    names = {p.name for p in (await client.list_prompts()).prompts}
    assert names == {"profile_brief", "topic_pulse"}

    prompt = await client.get_prompt("topic_pulse", {"topic": "MCP"})
    message = prompt.messages[0].content.text
    assert "x_search_recent_posts" in message and "lang:en" in message
