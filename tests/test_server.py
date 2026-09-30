"""Tests through the MCP protocol, in-process: what a client sees, minus HTTP and auth."""

from __future__ import annotations

import json
from dataclasses import replace

import anyio
import pytest
from mcp import Client, MCPError
from mcp.client.subscriptions import ResourceUpdated
from mcp.types import ElicitResult, PromptReference, ResourceTemplateReference
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS

from x_advanced_http import server

pytestmark = pytest.mark.anyio

READ_TOOLS = {
    "x_get_user",
    "x_get_users",
    "x_get_post",
    "x_get_posts",
    "x_get_user_posts",
    "x_get_user_mentions",
    "x_search_recent_posts",
    "x_search_digest",
}
WRITE_TOOLS = {"x_create_post", "x_delete_post"}


def text(result) -> str:
    return "\n".join(block.text for block in result.content)


# --- Listing ------------------------------------------------------------------------------


async def test_every_tool_is_typed_and_annotated(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for name, tool in tools.items():
        assert tool.description and tool.title
        assert tool.output_schema["type"] == "object"
        assert tool.annotations.read_only_hint is (name in READ_TOOLS)
    assert tools["x_delete_post"].annotations.destructive_hint is True
    assert tools["x_create_post"].annotations.idempotent_hint is False
    assert "next_cursor" in tools["x_search_recent_posts"].output_schema["properties"]


async def test_resolved_parameters_are_hidden_from_the_model(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    create = tools["x_create_post"].input_schema["properties"]
    assert set(create) == {"text", "reply_to_post_id"}  # no `x`, no `confirmation`


async def test_without_a_user_token_there_are_no_write_tools(config, fake_x):
    read_only = server.create_server(replace(config, x_user_access_token=None))
    async with Client(read_only) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        uris = {str(r.uri) for r in (await c.list_resources()).resources}
        assert names == READ_TOOLS
        assert "x://me/posts" not in uris
        assert "publish" not in c.instructions


# --- Structured output --------------------------------------------------------------------


async def test_get_user_returns_structured_content(client):
    result = await client.call_tool("x_get_user", {"username": "@XDevelopers"})
    assert not result.is_error
    user = result.structured_content
    assert user["id"] == "2244994945"
    assert user["followers"] == 570000
    assert user["joined"] == "2013-12-14"
    assert json.loads(text(result)) == user  # the model reads the same object as JSON


async def test_user_lookups_are_cached(client, fake_x):
    await client.call_tool("x_get_user", {"username": "XDevelopers"})
    await client.call_tool("x_get_user_posts", {"username": "xdevelopers"})
    lookups = [r for r in fake_x.requests if "/users/by/username/" in r.url.path]
    assert len(lookups) == 1


async def test_batch_users_report_missing(client):
    result = await client.call_tool("x_get_users", {"usernames": ["XDevelopers", "ghost"]})
    assert [u["username"] for u in result.structured_content["users"]] == ["XDevelopers"]
    assert result.structured_content["not_found"] == ["ghost"]


async def test_posts_carry_urls_and_cursor(client, fake_x):
    first = await client.call_tool("x_get_user_posts", {"username": "XDevelopers"})
    page = first.structured_content
    assert page["next_cursor"] == "page2"
    assert page["posts"][0]["url"] == "https://x.com/XDevelopers/status/1460323737035677698"
    assert fake_x.requests[-1].url.params["exclude"] == "replies,retweets"

    second = await client.call_tool(
        "x_get_user_posts", {"username": "XDevelopers", "cursor": "page2"}
    )
    assert second.structured_content["next_cursor"] is None


async def test_errors_are_tool_errors_the_model_can_read(client):
    missing = await client.call_tool("x_get_user", {"username": "nobody_here"})
    assert missing.is_error and "No X user named @nobody_here" in text(missing)

    limited = await client.call_tool("x_search_recent_posts", {"query": "rate-limited"})
    assert limited.is_error and "rate limit" in text(limited)
    assert "resets in 0 seconds" in text(limited)  # the reset time in the fake is in the past


async def test_mentions_like_server_01(client, fake_x):
    result = await client.call_tool("x_get_user_mentions", {"username": "XDevelopers"})
    assert not result.is_error, text(result)
    assert fake_x.requests[-1].url.path == "/2/users/2244994945/mentions"
    assert result.structured_content["next_cursor"] == "page2"


async def test_long_posts_show_their_full_text(client):
    result = await client.call_tool("x_get_post", {"post_id": "1700000000000000001"})
    assert result.structured_content["text"] == "A long post that X cuts short, full ending here."


async def test_only_a_missing_user_is_resource_not_found(client):
    # ResourceNotFoundError reaches the client as -32602; any other ResourceError as -32603.
    with pytest.raises(MCPError) as missing:
        await client.read_resource("x://users/nobody_here")
    assert missing.value.error.code == INVALID_PARAMS

    with pytest.raises(MCPError) as limited:
        await client.read_resource("x://users/ratelimited")
    assert limited.value.error.code == INTERNAL_ERROR
    assert "rate limit" in limited.value.error.message


async def test_invalid_arguments_never_reach_x(client, fake_x):
    result = await client.call_tool("x_search_digest", {"query": "mcp", "max_posts": 5000})
    assert result.is_error
    assert fake_x.requests == []


async def test_transient_failures_are_retried(client, fake_x):
    fake_x.fail_next = [503, 502]
    result = await client.call_tool("x_get_post", {"post_id": "1460323737035677698"})
    assert not result.is_error
    assert len(fake_x.requests) == 3


async def test_retries_are_bounded(client, fake_x):
    fake_x.fail_next = [503, 503, 503]
    result = await client.call_tool("x_get_post", {"post_id": "1460323737035677698"})
    assert result.is_error and "503" in text(result)


# --- Progress -----------------------------------------------------------------------------


async def test_digest_pages_through_results_and_reports_progress(client, fake_x):
    updates: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        updates.append((progress, total, message))

    result = await client.call_tool(
        "x_search_digest", {"query": "digest", "max_posts": 500}, progress_callback=on_progress
    )
    digest = result.structured_content
    assert (digest["posts_scanned"], digest["pages_fetched"], digest["complete"]) == (250, 3, True)
    assert digest["top_posts"][0]["likes"] == 249
    assert digest["languages"] == {"es": 125, "en": 125}
    assert digest["top_authors"][0] == {"username": "XDevelopers", "posts": 166}
    assert [u[0] for u in updates] == [100, 200, 250]  # always increasing
    assert updates[-1][1] == 500


async def test_digest_stops_at_the_limit(client, fake_x):
    result = await client.call_tool("x_search_digest", {"query": "digest", "max_posts": 150})
    digest = result.structured_content
    assert (digest["posts_scanned"], digest["complete"]) == (150, False)
    assert [int(r.url.params["max_results"]) for r in fake_x.requests] == [100, 50]


# --- Writes: confirmation through elicitation ---------------------------------------------


async def test_create_post_asks_first_then_posts(client, fake_x, answers):
    result = await client.call_tool("x_create_post", {"text": "Hello from MCP"})
    assert not result.is_error, text(result)
    assert client.protocol_version == "2026-07-28"  # the question rode a multi-round-trip
    assert result.structured_content["status"] == "posted"
    assert result.structured_content["post_id"] == "5555"
    assert answers.questions == ["Publish this post on X?\n\nHello from MCP"]
    assert fake_x.created == [{"text": "Hello from MCP"}]


async def test_confirmation_works_for_legacy_clients_too(config, fake_x, answers):
    # A 2025-11-25 client gets the question as a live elicitation request mid-call; a
    # 2026-07-28 client gets it inside the tools/call result and retries. Same tool code.
    legacy = Client(server.create_server(config), mode="legacy", elicitation_callback=answers)
    async with legacy as c:
        result = await c.call_tool("x_create_post", {"text": "Old client"})
        assert c.protocol_version != "2026-07-28"
    assert result.structured_content["status"] == "posted"
    assert len(answers.questions) == 1


async def test_reply_says_what_it_replies_to(client, fake_x, answers):
    await client.call_tool("x_create_post", {"text": "Agreed", "reply_to_post_id": "42"})
    assert "as a reply to post 42" in answers.questions[0]
    assert fake_x.created == [{"text": "Agreed", "reply": {"in_reply_to_tweet_id": "42"}}]


async def test_answering_no_posts_nothing(client, fake_x, answers):
    answers.next = ElicitResult(action="accept", content={"confirm": False})
    result = await client.call_tool("x_create_post", {"text": "Maybe not"})
    assert result.structured_content["status"] == "cancelled"
    assert fake_x.created == []


async def test_declining_the_question_fails_the_call(client, fake_x, answers):
    answers.next = ElicitResult(action="decline")
    result = await client.call_tool("x_create_post", {"text": "Nope"})
    assert result.is_error
    assert fake_x.created == []


async def test_delete_post_is_confirmed(client, fake_x, answers):
    result = await client.call_tool("x_delete_post", {"post_id": "5555"})
    assert result.structured_content == {
        "status": "deleted",
        "post_id": "5555",
        "url": None,
        "text": None,
    }
    assert "cannot be undone" in answers.questions[0]
    assert fake_x.requests[-1].method == "DELETE"


async def test_posts_longer_than_280_code_points_reach_x(client, fake_x):
    # X weights characters and Premium allows more, so X, not the schema, decides.
    result = await client.call_tool("x_create_post", {"text": "a" * 300})
    assert result.structured_content["status"] == "posted"


async def test_delete_reported_as_not_deleted_but_gone_is_a_success(client, fake_x):
    # What a retried DELETE sees when the first attempt worked but its response was lost.
    fake_x.delete_reports_deleted = False
    result = await client.call_tool("x_delete_post", {"post_id": "5555"})  # unknown to X
    assert result.structured_content["status"] == "deleted"


async def test_delete_reported_as_not_deleted_and_still_there_fails(client, fake_x):
    fake_x.delete_reports_deleted = False
    result = await client.call_tool("x_delete_post", {"post_id": "1460323737035677698"})
    assert result.is_error and "did not delete" in text(result)


async def test_writes_notify_subscribers_of_my_posts(client, fake_x):
    async with client.listen(resource_subscriptions=["x://me/posts"]) as events:
        await client.call_tool("x_create_post", {"text": "Ping"})
        with anyio.fail_after(5):
            event = await anext(aiter(events))
    assert isinstance(event, ResourceUpdated)
    assert str(event.uri) == "x://me/posts"

    posts = await client.read_resource("x://me/posts")
    assert json.loads(posts.contents[0].text)["posts"]
    await client.read_resource("x://me/posts")
    assert [r.url.path for r in fake_x.requests].count("/2/users/me") == 1  # ID looked up once


# --- Resources, prompts, completions ------------------------------------------------------


async def test_resources(client):
    guide = await client.read_resource("x://guides/search-operators")
    assert "from:" in guide.contents[0].text
    profile = await client.read_resource("x://users/XDevelopers")
    assert json.loads(profile.contents[0].text)["username"] == "XDevelopers"


async def test_prompts_point_at_the_tools(client):
    prompt = await client.get_prompt("topic_pulse", {"topic": "MCP", "language": "de"})
    message = prompt.messages[0].content.text
    assert "x_search_digest" in message and "lang:de" in message


async def test_completions(client):
    languages = await client.complete(
        PromptReference(name="topic_pulse"), {"name": "language", "value": "e"}
    )
    assert languages.completion.values == ["en", "es"]

    template = ResourceTemplateReference(uri="x://users/{username}")
    before = await client.complete(template, {"name": "username", "value": ""})
    assert before.completion.values == []  # nothing looked up yet

    await client.call_tool("x_get_users", {"usernames": ["XDevelopers", "XApi"]})
    after = await client.complete(template, {"name": "username", "value": "xd"})
    assert after.completion.values == ["XDevelopers"]
