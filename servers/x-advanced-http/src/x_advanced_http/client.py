"""An async client for the parts of the X API v2 this server uses.

It knows nothing about MCP. Compared with server 01's client it adds writes (create and delete a
post, which need a user-context token), bounded retries for transient failures, and a small TTL
cache for user lookups.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import anyio
import httpx
from mcp.server.mcpserver.exceptions import ToolError

logger = logging.getLogger(__name__)

API_BASE = "https://api.x.com/2"
USER_AGENT = "x-advanced-http-mcp"

USER_FIELDS = "created_at,description,location,public_metrics,verified,url"
POST_FIELDS = "created_at,author_id,public_metrics,conversation_id,lang"

# Statuses worth one more try: the request never reached X's application logic, or X said so.
RETRY_STATUSES = {502, 503, 504}
RETRY_BACKOFF = 0.5  # seconds before the first retry; doubles each time


class XApiError(ToolError):
    """An X API failure, worded so the model can decide what to do next."""


class XClient:
    def __init__(
        self,
        token: str,
        *,
        token_env: str,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 2,
    ) -> None:
        self._token_env = token_env
        self._max_retries = max_retries
        self._http = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            timeout=httpx.Timeout(15.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a request and return the JSON body, or raise `XApiError`.

        Retries transient failures (timeouts, connection errors, 502/503/504) with exponential
        backoff, but only for GET and DELETE: retrying a POST could publish the same post twice.
        """
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        retryable = method in {"GET", "DELETE"}
        attempt = 0
        while True:
            try:
                response = await self._http.request(method, path, params=clean, json=json)
            except httpx.TimeoutException as exc:
                if retryable and attempt < self._max_retries:
                    attempt += 1
                    await self._sleep(attempt, f"timeout on {method} {path}")
                    continue
                raise XApiError("The X API timed out. Try again in a moment.") from exc
            except httpx.HTTPError as exc:
                if retryable and attempt < self._max_retries:
                    attempt += 1
                    await self._sleep(attempt, f"{exc.__class__.__name__} on {method} {path}")
                    continue
                raise XApiError(f"Could not reach the X API: {exc.__class__.__name__}.") from exc

            if response.status_code in RETRY_STATUSES and retryable and attempt < self._max_retries:
                attempt += 1
                await self._sleep(attempt, f"HTTP {response.status_code} on {method} {path}")
                continue
            if response.is_success:
                return response.json()
            raise XApiError(describe_http_error(response, self._token_env))

    async def _sleep(self, attempt: int, reason: str) -> None:
        delay = RETRY_BACKOFF * 2 ** (attempt - 1)
        logger.warning("Retrying X API call in %.1fs (attempt %d): %s", delay, attempt, reason)
        await anyio.sleep(delay)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("GET", path, params=params)

    # --- Reads (app-only token) ------------------------------------------------------------

    async def user_by_username(self, username: str) -> dict[str, Any]:
        body = await self.get(f"/users/by/username/{username}", {"user.fields": USER_FIELDS})
        return require_data(body, f"No X user named @{username}.")

    async def users_by_usernames(self, usernames: list[str]) -> dict[str, Any]:
        return await self.get(
            "/users/by", {"usernames": ",".join(usernames), "user.fields": USER_FIELDS}
        )

    async def post(self, post_id: str) -> dict[str, Any]:
        body = await self.get(f"/tweets/{post_id}", post_params())
        return require_data(body, f"No X post with ID {post_id} (deleted, private or wrong ID).")

    async def posts(self, post_ids: list[str]) -> dict[str, Any]:
        return await self.get("/tweets", {"ids": ",".join(post_ids), **post_params()})

    async def user_posts(
        self, user_id: str, max_results: int, cursor: str | None, exclude: str | None
    ) -> dict[str, Any]:
        params = {"max_results": max_results, "pagination_token": cursor, "exclude": exclude}
        return await self.get(f"/users/{user_id}/tweets", {**params, **post_params()})

    async def search_recent(
        self, query: str, max_results: int, cursor: str | None, sort_order: str
    ) -> dict[str, Any]:
        params = {
            "query": query,
            "max_results": max_results,
            "next_token": cursor,
            "sort_order": sort_order,
        }
        return await self.get("/tweets/search/recent", {**params, **post_params()})

    # --- User context (user access token) --------------------------------------------------

    async def me(self) -> dict[str, Any]:
        body = await self.get("/users/me", {"user.fields": USER_FIELDS})
        return require_data(body, "X did not return the signed-in user.")

    async def create_post(self, text: str, reply_to: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}
        return await self.request("POST", "/tweets", json=payload)

    async def delete_post(self, post_id: str) -> dict[str, Any]:
        return await self.request("DELETE", f"/tweets/{post_id}")


class UserCache:
    """Remember recent user lookups for a few minutes.

    It saves the extra lookup that timeline tools need (handle -> user id), and it is where
    completions get handles to suggest. Keyed by lowercase handle, since X handles are
    case-insensitive.
    """

    def __init__(self, ttl: float = 300.0, max_size: int = 1000) -> None:
        self._ttl = ttl
        self._max_size = max_size
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, username: str) -> dict[str, Any] | None:
        item = self._items.get(username.lower())
        if item is None or item[0] < time.monotonic():
            return None
        return item[1]

    def put(self, user: dict[str, Any]) -> None:
        if len(self._items) >= self._max_size:
            self._items.pop(next(iter(self._items)))  # drop the oldest entry
        self._items[user["username"].lower()] = (time.monotonic() + self._ttl, user)

    def handles(self, prefix: str = "") -> list[str]:
        now = time.monotonic()
        prefix = prefix.lower().removeprefix("@")
        return sorted(
            user["username"]
            for key, (expires, user) in self._items.items()
            if expires >= now and key.startswith(prefix)
        )


def post_params() -> dict[str, str]:
    return {"tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": "username"}


def require_data(body: dict[str, Any], not_found: str) -> dict[str, Any]:
    # X answers HTTP 200 with an `errors` array (and no `data`) when an item does not exist.
    if "data" not in body:
        raise XApiError(not_found)
    return body


def describe_http_error(response: httpx.Response, token_env: str) -> str:
    status = response.status_code
    detail = ""
    try:
        body = response.json()
        detail = body.get("detail") or body.get("title") or ""
    except ValueError:
        pass
    if status == 400:
        return f"X rejected the request: {detail or 'invalid parameters'}."
    if status == 401:
        return f"X rejected the credentials. Check {token_env}."
    if status == 403:
        return (
            "X refused access (403). Your API plan or token scopes may not allow this. "
            f"{detail}".strip()
        )
    if status == 404:
        return "X could not find that resource."
    if status == 429:
        reset = response.headers.get("x-rate-limit-reset")
        when = f" The limit resets at Unix time {reset}." if reset else ""
        return f"X rate limit reached.{when} Wait before retrying."
    return f"X API error {status}. {detail}".strip()
