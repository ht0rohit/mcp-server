"""A thin async client for the read-only parts of the X API v2.

It knows nothing about MCP. Keeping the HTTP code here keeps `server.py` about MCP only, and
lets tests swap the network for an `httpx.MockTransport`.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

API_BASE = "https://api.x.com/2"
TOKEN_ENV = "X_BEARER_TOKEN"

# Fields requested on every call, so every tool can show the same details.
USER_FIELDS = "created_at,description,location,public_metrics,verified,url"
POST_FIELDS = "created_at,author_id,public_metrics,conversation_id,lang"


class XApiError(ToolError):
    """An X API failure, worded so the model can decide what to do next.

    Subclassing `ToolError` means the SDK turns it into a tool result with `is_error: true`
    and this message, instead of a generic crash.
    """


class XClient:
    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "x-basic-stdio-mcp"},
            timeout=httpx.Timeout(15.0),
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> XClient:
        token = os.environ.get(TOKEN_ENV, "").strip()
        if not token:
            raise RuntimeError(f"{TOKEN_ENV} is not set. Create an app at developer.x.com.")
        return cls(token)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET an endpoint and return its JSON body, or raise `XApiError`."""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            response = await self._http.get(path, params=clean)
        except httpx.TimeoutException as exc:
            raise XApiError("The X API timed out. Try again in a moment.") from exc
        except httpx.HTTPError as exc:
            raise XApiError(f"Could not reach the X API: {exc.__class__.__name__}.") from exc

        if response.status_code == 200:
            return response.json()
        raise XApiError(_describe_http_error(response))

    # One method per endpoint the tools use. Each returns the raw X JSON.

    async def user_by_username(self, username: str) -> dict[str, Any]:
        body = await self.get(f"/users/by/username/{username}", {"user.fields": USER_FIELDS})
        return _require_data(body, f"No X user named @{username}.")

    async def users_by_usernames(self, usernames: list[str]) -> dict[str, Any]:
        return await self.get(
            "/users/by", {"usernames": ",".join(usernames), "user.fields": USER_FIELDS}
        )

    async def post(self, post_id: str) -> dict[str, Any]:
        body = await self.get(f"/tweets/{post_id}", _post_params())
        return _require_data(body, f"No X post with ID {post_id} (deleted, private or wrong ID).")

    async def posts(self, post_ids: list[str]) -> dict[str, Any]:
        return await self.get("/tweets", {"ids": ",".join(post_ids), **_post_params()})

    async def user_posts(
        self, user_id: str, max_results: int, cursor: str | None, exclude: str | None
    ) -> dict[str, Any]:
        params = {"max_results": max_results, "pagination_token": cursor, "exclude": exclude}
        return await self.get(f"/users/{user_id}/tweets", {**params, **_post_params()})

    async def user_mentions(self, user_id: str, max_results: int, cursor: str | None) -> dict:
        params = {"max_results": max_results, "pagination_token": cursor}
        return await self.get(f"/users/{user_id}/mentions", {**params, **_post_params()})

    async def search_recent(
        self, query: str, max_results: int, cursor: str | None, sort_order: str
    ) -> dict[str, Any]:
        params = {
            "query": query,
            "max_results": max_results,
            "next_token": cursor,
            "sort_order": sort_order,
        }
        return await self.get("/tweets/search/recent", {**params, **_post_params()})


def _post_params() -> dict[str, str]:
    # `expansions=author_id` makes X include the authors in `includes.users`, so a post can be
    # shown with its @handle without a second request.
    return {"tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": "username"}


def _require_data(body: dict[str, Any], not_found: str) -> dict[str, Any]:
    # X answers HTTP 200 with an `errors` array (and no `data`) when an item does not exist.
    if "data" not in body:
        raise XApiError(not_found)
    return body


def _describe_http_error(response: httpx.Response) -> str:
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
        return f"X rejected the credentials. Check {TOKEN_ENV}."
    if status == 403:
        return (
            f"X refused access (403). Your API plan may not include this endpoint. {detail}".strip()
        )
    if status == 404:
        return "X could not find that resource."
    if status == 429:
        reset = response.headers.get("x-rate-limit-reset")
        when = f" The limit resets at Unix time {reset}." if reset else ""
        return f"X rate limit reached.{when} Wait before retrying."
    return f"X API error {status}. {detail}".strip()
