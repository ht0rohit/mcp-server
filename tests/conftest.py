"""Shared fixtures: a fake X API, an in-process MCP client, and the real HTTP app in-process."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client import ClientRequestContext
from mcp.client.streamable_http import streamable_http_client
from mcp.types import ElicitRequestParams, ElicitResult

from x_advanced_http import client as x_client
from x_advanced_http import server
from x_advanced_http.config import Config

READ_TOKEN = "read-token-0123456789"
WRITE_TOKEN = "write-token-0123456789"
PUBLIC_URL = "http://127.0.0.1:8000/mcp"

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
    "xapi": {"id": "111", "username": "XApi", "name": "X API"},
}
ME = {"id": "999", "username": "me_on_x", "name": "Me"}
POSTS = {
    "1460323737035677698": {
        "id": "1460323737035677698",
        "text": "Introducing a new era for the X API",
        "author_id": "2244994945",
        "created_at": "2021-11-15T19:08:05.000Z",
        "lang": "en",
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
        "text": "A long post that X cuts short…",
        "note_tweet": {"text": "A long post that X cuts short, full ending here."},
        "author_id": "2244994945",
    },
}
AUTHORS = {"users": [{"id": "2244994945", "username": "XDevelopers"}, ME]}


def not_found(value: str) -> dict:
    # What X really sends for a missing item: HTTP 200, no `data`, an `errors` array.
    return {"errors": [{"value": value, "title": "Not Found Error"}]}


def digest_page(params: httpx.QueryParams) -> dict:
    """250 matching posts, served 100 at a time: pages '', 'p2', 'p3'."""
    start = {"": 0, "p2": 100, "p3": 200}[params.get("next_token", "")]
    size = min(int(params["max_results"]), 250 - start)
    posts = [
        {
            "id": str(1000 + i),
            "text": f"post {i}",
            "author_id": "2244994945" if i % 3 else "999",
            "lang": "en" if i % 2 else "es",
            "public_metrics": {"like_count": i},
        }
        for i in range(start, start + size)
    ]
    page = {"data": posts, "includes": AUTHORS, "meta": {"result_count": size}}
    following = {0: "p2", 100: "p3"}.get(start)
    if following and start + size < 250:
        page["meta"]["next_token"] = following
    return page


class FakeX:
    """Answers the X endpoints the server calls, like the real API would."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.created: list[dict] = []
        self.fail_next: list[int] = []  # status codes to return before answering normally
        self.delete_reports_deleted = True  # False: X answers `deleted: false`

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), json={"title": "Service Unavailable"})
        path = request.url.path.removeprefix("/2")
        params = request.url.params
        auth = request.headers["Authorization"]

        if path == "/users/me":
            assert auth == "Bearer user-token", "user endpoints need the user token"
            return httpx.Response(200, json={"data": ME})
        if path.startswith("/users/by/username/"):
            name = path.rsplit("/", 1)[1]
            if name == "ratelimited":
                return httpx.Response(429, headers={"x-rate-limit-reset": "9999999999"}, json={})
            user = USERS.get(name.lower())
            return httpx.Response(200, json={"data": user} if user else not_found(name))
        if path == "/users/by":
            names = params["usernames"].split(",")
            missing = [n for n in names if n.lower() not in USERS]
            body: dict = {"errors": [not_found(n)["errors"][0] for n in missing]}
            if found := [USERS[n.lower()] for n in names if n.lower() in USERS]:
                body["data"] = found
            return httpx.Response(200, json=body)
        if path == "/tweets" and request.method == "POST":
            assert auth == "Bearer user-token"
            self.created.append(json.loads(request.content))
            return httpx.Response(201, json={"data": {"id": "5555", "text": "posted"}})
        if path.startswith("/tweets/") and request.method == "DELETE":
            assert auth == "Bearer user-token"
            return httpx.Response(200, json={"data": {"deleted": self.delete_reports_deleted}})
        if path == "/tweets/search/recent":
            query = params["query"]
            if query == "rate-limited":
                return httpx.Response(429, headers={"x-rate-limit-reset": "1790000000"}, json={})
            if query == "nothing":
                return httpx.Response(200, json={"meta": {"result_count": 0}})
            if query == "digest":
                return httpx.Response(200, json=digest_page(params))
        if path.startswith("/tweets/"):
            post_id = path.rsplit("/", 1)[1]
            post = POSTS.get(post_id)
            body = {"data": post, "includes": AUTHORS} if post else not_found(post_id)
            return httpx.Response(200, json=body)
        if path == "/tweets":
            ids = params["ids"].split(",")
            body = {"errors": [not_found(i)["errors"][0] for i in ids if i not in POSTS]}
            if found := [POSTS[i] for i in ids if i in POSTS]:
                body |= {"data": found, "includes": AUTHORS}
            return httpx.Response(200, json=body)
        if path.endswith(("/tweets", "/mentions")) or path == "/tweets/search/recent":
            page = {"data": list(POSTS.values()), "includes": AUTHORS, "meta": {"result_count": 1}}
            if "pagination_token" not in params and "next_token" not in params:
                page["meta"]["next_token"] = "page2"
            return httpx.Response(200, json=page)
        return httpx.Response(404, json={"title": "Not Found"})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def config() -> Config:
    return Config(
        x_bearer_token="app-token",
        x_user_access_token="user-token",
        auth_tokens={
            READ_TOKEN: frozenset({"x:read"}),
            WRITE_TOKEN: frozenset({"x:read", "x:write"}),
        },
        public_url=PUBLIC_URL,
    )


@pytest.fixture
def fake_x(monkeypatch) -> FakeX:
    fake = FakeX()
    monkeypatch.setattr(server, "make_transport", lambda: httpx.MockTransport(fake))
    # No real waiting between retries in tests.
    monkeypatch.setattr(x_client, "RETRY_BACKOFF", 0)
    return fake


class Answers:
    """What the fake user answers when the server asks a question (elicitation)."""

    def __init__(self) -> None:
        self.next = ElicitResult(action="accept", content={"confirm": True})
        self.questions: list[str] = []

    async def __call__(
        self, _ctx: ClientRequestContext, params: ElicitRequestParams
    ) -> ElicitResult:
        self.questions.append(params.message)
        return self.next


@pytest.fixture
def answers() -> Answers:
    return Answers()


@pytest.fixture
async def client(config, fake_x, answers) -> AsyncIterator[Client]:
    """In-process: no HTTP and no auth, just the MCP protocol against the server object."""
    async with Client(server.create_server(config), elicitation_callback=answers) as c:
        yield c


@pytest.fixture
def app(config, fake_x):
    return server.create_app(config)


@pytest.fixture
async def http(app) -> AsyncIterator[httpx2.AsyncClient]:
    """Plain HTTP against the real ASGI app (routing, auth, Host checks), in-process."""
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            yield c


@pytest.fixture
def connect(app, answers):
    """Open an MCP client over Streamable HTTP to the in-process app, with a read or write token."""

    def open_client(token_kind: str):
        token = {"read": READ_TOKEN, "write": WRITE_TOKEN}[token_kind]
        http_client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=PUBLIC_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        return http_client, Client(
            streamable_http_client(PUBLIC_URL, http_client=http_client),
            elicitation_callback=answers,
        )

    return open_client
