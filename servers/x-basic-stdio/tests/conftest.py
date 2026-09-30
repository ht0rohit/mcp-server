"""Shared fixtures: a fake X API and an in-process MCP client connected to the server."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from mcp import Client
from x_basic_stdio import server
from x_basic_stdio.client import XClient

USERS = {
    "xdevelopers": {
        "id": "2244994945",
        "username": "XDevelopers",
        "name": "Developers",
        "description": "The voice of the X Dev team",
        "verified": True,
        "created_at": "2013-12-14T04:35:55.000Z",
        "public_metrics": {"followers_count": 570000, "following_count": 2000, "tweet_count": 3900},
    },
}
POSTS = {
    "1460323737035677698": {
        "id": "1460323737035677698",
        "text": "Introducing a new era for the X API",
        "author_id": "2244994945",
        "created_at": "2021-11-15T19:08:05.000Z",
        "public_metrics": {
            "like_count": 10,
            "retweet_count": 2,
            "reply_count": 1,
            "quote_count": 0,
        },
    },
    # A long post: X cuts `text` at 280 characters and puts the full text in `note_tweet`.
    "1700000000000000001": {
        "id": "1700000000000000001",
        "text": "A long post that X cuts short in the text field…",
        "note_tweet": {"text": "A long post that X cuts short, full ending here."},
        "author_id": "2244994945",
        "created_at": "2023-09-01T10:00:00.000Z",
        "public_metrics": {"like_count": 1},
    },
}
AUTHORS = {"users": [{"id": "2244994945", "username": "XDevelopers"}]}


def not_found(value: str) -> dict:
    # What X really sends for a missing item: HTTP 200, no `data`, an `errors` array.
    return {
        "errors": [
            {"value": value, "title": "Not Found Error", "detail": f"Could not find {value}"}
        ]
    }


def fake_x_api(request: httpx.Request) -> httpx.Response:
    """Answer the handful of X endpoints the server calls, like the real API would."""
    path = request.url.path.removeprefix("/2")
    params = request.url.params
    assert request.headers["Authorization"] == "Bearer test-token"

    if path.startswith("/users/by/username/"):
        name = path.rsplit("/", 1)[1]
        if name == "ratelimited":
            return httpx.Response(429, headers={"x-rate-limit-reset": "9999999999"}, json={})
        user = USERS.get(name.lower())
        return httpx.Response(200, json={"data": user} if user else not_found(name))
    if path == "/users/by":
        names = params["usernames"].split(",")
        found = [USERS[n.lower()] for n in names if n.lower() in USERS]
        missing = [n for n in names if n.lower() not in USERS]
        body: dict = {"errors": [not_found(n)["errors"][0] for n in missing]} if missing else {}
        if found:
            body["data"] = found
        return httpx.Response(200, json=body)
    if path.startswith("/tweets/") and path != "/tweets/search/recent":
        post = POSTS.get(path.rsplit("/", 1)[1])
        body = {"data": post, "includes": AUTHORS} if post else not_found(path.rsplit("/", 1)[1])
        return httpx.Response(200, json=body)
    if path == "/tweets":
        ids = params["ids"].split(",")
        found = [POSTS[i] for i in ids if i in POSTS]
        body = {"errors": [not_found(i)["errors"][0] for i in ids if i not in POSTS]}
        if found:
            body |= {"data": found, "includes": AUTHORS}
        return httpx.Response(200, json=body)
    if path.endswith("/tweets") or path.endswith("/mentions") or path == "/tweets/search/recent":
        if params.get("query") == "rate-limited":
            return httpx.Response(429, headers={"x-rate-limit-reset": "1790000000"}, json={})
        if params.get("query") == "forbidden":
            return httpx.Response(403, json={"title": "Forbidden", "detail": "Not in your plan"})
        if params.get("query") == "nothing":
            return httpx.Response(200, json={"meta": {"result_count": 0}})
        page = {"data": list(POSTS.values()), "includes": AUTHORS, "meta": {"result_count": 1}}
        if "pagination_token" not in params and "next_token" not in params:
            page["meta"]["next_token"] = "page2"
        return httpx.Response(200, json=page)
    return httpx.Response(404, json={"title": "Not Found"})


@pytest.fixture
def anyio_backend() -> str:
    # The MCP SDK is built on anyio; its pytest plugin runs each test and its fixtures in one
    # task, which the client's cancel scopes need. Mark async tests with `pytest.mark.anyio`.
    return "asyncio"


@pytest.fixture
def requests() -> list[httpx.Request]:
    return []


@pytest.fixture
async def client(monkeypatch, requests) -> AsyncIterator[Client]:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return fake_x_api(request)

    monkeypatch.setattr(
        server, "make_client", lambda: XClient("test-token", transport=httpx.MockTransport(handler))
    )
    async with Client(server.mcp) as c:
        yield c
