"""x-basic-stdio: a read-only MCP server for X (Twitter), over stdio.

The basics of an MCP server, all in this one file:

1. A server object with a name and instructions.       -> `mcp = MCPServer(...)`
2. A lifespan that opens shared resources once.        -> `lifespan()`
3. Tools: functions the model can call.                -> `@mcp.tool(...)`
4. Resources: data the client can read by URI.         -> `@mcp.resource(...)`
5. Prompts: reusable message templates for the user.   -> `@mcp.prompt(...)`
6. Running over stdio.                                 -> `main()`
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp_types import ToolAnnotations
from pydantic import Field

from .client import TOKEN_ENV, XApiError, XClient
from .formatting import (
    format_missing,
    format_post,
    format_post_page,
    format_user,
    join_within_limit,
)

logger = logging.getLogger(__name__)


# --- 1. Shared state, opened once for the life of the server ------------------------------


@dataclass
class AppContext:
    x: XClient


def make_client() -> XClient:
    """Build the X client. Tests replace this to inject a mocked transport."""
    return XClient.from_env()


@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
    # One HTTP client (and its connection pool) for every call, closed on shutdown.
    client = make_client()
    try:
        yield AppContext(x=client)
    finally:
        await client.aclose()


def x_client(ctx: Context) -> XClient:
    return ctx.request_context.lifespan_context.x


# --- 2. The server ------------------------------------------------------------------------

mcp = MCPServer(
    name="x-basic-stdio",
    title="X (read-only)",
    version="0.1.0",
    # Instructions are sent to the client once and usually end up in the model's context:
    # say what the server is for and how its tools fit together.
    instructions=(
        "Read-only access to X (Twitter): look up users and posts, list a user's recent posts "
        "and mentions, and search posts from the last 7 days. Usernames may include '@'. "
        "List tools return a cursor when more results exist; pass it back to get the next page. "
        "Read the resource x://guides/search-operators before writing complex search queries."
    ),
    lifespan=lifespan,
)


# --- 3. Tools -----------------------------------------------------------------------------
#
# Best practices shown below:
# - Prefixed, verb-first names (`x_get_user`) so they don't clash with other servers' tools.
# - The docstring is the description the model reads: say what it returns and when to use it.
# - Flat, annotated arguments. Pydantic validates them before your code runs, and the limits
#   (`ge`, `le`, `pattern`) appear in the input schema so the model can get them right first time.
# - Annotations tell the client these tools only read, are safe to retry, and reach the internet.
# - Expected failures raise `ToolError` (here `XApiError`), so the result has `is_error: true`.
# - `structured_output=False`: these tools return plain text. Typed output comes in a later server.

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

Username = Annotated[
    str,
    Field(
        description="X handle, with or without '@', e.g. 'XDevelopers'",
        pattern=r"^@?[A-Za-z0-9_]{1,15}$",
    ),
]
PostId = Annotated[
    str, Field(description="Numeric X post ID, e.g. '1460323737035677698'", pattern=r"^\d{1,19}$")
]
Cursor = Annotated[
    str | None, Field(description="Cursor from a previous call to get the next page")
]
TimelineSize = Annotated[int, Field(ge=5, le=100, description="Posts per page (5-100)")]


def clean_username(username: str) -> str:
    return username.removeprefix("@")


@mcp.tool(title="Get X user", annotations=READ_ONLY, structured_output=False)
async def x_get_user(username: Username, ctx: Context) -> str:
    """Get one X user's profile: name, ID, bio, location, join date and follower counts."""
    body = await x_client(ctx).user_by_username(clean_username(username))
    return format_user(body["data"])


@mcp.tool(title="Get several X users", annotations=READ_ONLY, structured_output=False)
async def x_get_users(
    usernames: Annotated[list[Username], Field(min_length=1, max_length=100)], ctx: Context
) -> str:
    """Get up to 100 X user profiles in one call. Prefer this over repeated x_get_user calls."""
    body = await x_client(ctx).users_by_usernames([clean_username(u) for u in usernames])
    users = body.get("data", [])
    if not users:
        raise XApiError("None of those X users exist.")
    text = join_within_limit([format_user(u) for u in users], separator="\n\n")
    return text + format_missing(body)


@mcp.tool(title="Get X post", annotations=READ_ONLY, structured_output=False)
async def x_get_post(post_id: PostId, ctx: Context) -> str:
    """Get one X post by ID: author, time, full text and engagement counts."""
    body = await x_client(ctx).post(post_id)
    authors = {u["id"]: u["username"] for u in body.get("includes", {}).get("users", [])}
    return format_post(body["data"], authors)


@mcp.tool(title="Get several X posts", annotations=READ_ONLY, structured_output=False)
async def x_get_posts(
    post_ids: Annotated[list[PostId], Field(min_length=1, max_length=100)], ctx: Context
) -> str:
    """Get up to 100 X posts by ID in one call."""
    body = await x_client(ctx).posts(post_ids)
    if not body.get("data"):
        raise XApiError("None of those X posts exist (deleted, private or wrong IDs).")
    return format_post_page(body, empty="") + format_missing(body)


@mcp.tool(title="List a user's posts", annotations=READ_ONLY, structured_output=False)
async def x_get_user_posts(
    username: Username,
    ctx: Context,
    max_results: TimelineSize = 10,
    include_replies: bool = False,
    include_reposts: bool = False,
    cursor: Cursor = None,
) -> str:
    """List an X user's most recent posts, newest first. Returns a cursor for more."""
    x = x_client(ctx)
    user = (await x.user_by_username(clean_username(username)))["data"]
    excluded = [
        name
        for name, include in (("replies", include_replies), ("retweets", include_reposts))
        if not include
    ]
    body = await x.user_posts(user["id"], max_results, cursor, ",".join(excluded) or None)
    return format_post_page(body, empty=f"@{user['username']} has no matching posts.")


@mcp.tool(title="List a user's mentions", annotations=READ_ONLY, structured_output=False)
async def x_get_user_mentions(
    username: Username,
    ctx: Context,
    max_results: TimelineSize = 10,
    cursor: Cursor = None,
) -> str:
    """List recent posts that mention an X user, newest first. Returns a cursor for more."""
    x = x_client(ctx)
    user = (await x.user_by_username(clean_username(username)))["data"]
    body = await x.user_mentions(user["id"], max_results, cursor)
    return format_post_page(body, empty=f"No recent posts mention @{user['username']}.")


@mcp.tool(title="Search recent X posts", annotations=READ_ONLY, structured_output=False)
async def x_search_recent_posts(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            description="X search query, e.g. 'from:XDevelopers -is:retweet' or '\"mcp\" lang:en'",
        ),
    ],
    ctx: Context,
    max_results: Annotated[int, Field(ge=10, le=100, description="Posts per page (10-100)")] = 10,
    sort_order: Literal["recency", "relevancy"] = "recency",
    cursor: Cursor = None,
) -> str:
    """Search X posts from the last 7 days. See x://guides/search-operators for query syntax."""
    body = await x_client(ctx).search_recent(query, max_results, cursor, sort_order)
    return format_post_page(body, empty=f"No posts in the last 7 days match {query!r}.")


# --- 4. Resources -------------------------------------------------------------------------
#
# Tools are called by the model; resources are read by the client (often chosen by the user)
# to add context. A fixed URI is a resource; a URI with {placeholders} is a resource template.

SEARCH_GUIDE = """\
# X search operators (recent search, last 7 days)

| Operator | Example | Matches |
|---|---|---|
| keyword | `mcp server` | posts containing both words |
| exact phrase | `"model context protocol"` | the exact phrase |
| OR | `python OR rust` | either word |
| exclude | `python -snake` | python but not snake |
| from: | `from:XDevelopers` | posts by an account |
| to: | `to:XDevelopers` | replies to an account |
| @ | `@XDevelopers` | posts mentioning an account |
| # | `#buildinpublic` | posts with a hashtag |
| lang: | `lang:en` | posts in a language |
| is:retweet | `-is:retweet` | exclude reposts |
| is:reply | `-is:reply` | exclude replies |
| has:links | `has:links` | posts with a link |
| has:media | `has:media` | posts with images or video |

Group with parentheses: `(python OR rust) lang:en -is:retweet`. Queries are at most 512 characters.
"""


@mcp.resource(
    "x://guides/search-operators",
    title="X search operators",
    description="Cheat sheet for writing x_search_recent_posts queries",
    mime_type="text/markdown",
)
def search_operators() -> str:
    return SEARCH_GUIDE


@mcp.resource(
    "x://users/{username}",
    title="X user profile",
    description="An X user's public profile as JSON",
    mime_type="application/json",
)
async def user_profile(username: str, ctx: Context) -> str:
    try:
        body = await x_client(ctx).user_by_username(clean_username(username))
    except XApiError as exc:
        # Resources report failures with ResourceError / ResourceNotFoundError, not ToolError.
        raise ResourceNotFoundError(str(exc)) from exc
    return json.dumps(body["data"], indent=2)


# --- 5. Prompts ---------------------------------------------------------------------------
#
# Prompts are templates the user picks (for example as slash commands). They fill in a
# message that steers the model toward this server's tools.


@mcp.prompt(title="Profile brief")
def profile_brief(username: str) -> str:
    """Summarize who an X user is and what they have been posting about lately."""
    handle = clean_username(username)
    return (
        f"Write a short brief on the X user @{handle}.\n"
        f"1. Use x_get_user for their profile.\n"
        f"2. Use x_get_user_posts with max_results 20 for their recent posts.\n"
        "Then summarize in under 200 words: who they are, the main topics they post about, "
        "and their most engaging recent post (with its ID)."
    )


@mcp.prompt(title="Topic pulse")
def topic_pulse(topic: str, language: str = "en") -> str:
    """See what people on X are saying about a topic this week."""
    return (
        f"Find out what people on X are saying about {topic!r} this week.\n"
        f"Read x://guides/search-operators, then use x_search_recent_posts with a query that "
        f"targets the topic, adds lang:{language} and excludes reposts. Fetch up to 50 posts, "
        "sorted by relevancy.\n"
        "Report the 3 to 5 main themes, the overall tone, and quote 3 representative posts "
        "with their IDs."
    )


# --- 6. Run -------------------------------------------------------------------------------


def main() -> None:
    # stdout carries the MCP protocol over stdio, so anything else we print must go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    # Fail fast with a clear message instead of failing on the first tool call.
    if not os.environ.get(TOKEN_ENV, "").strip():
        print(f"x-basic-stdio: set {TOKEN_ENV} (an X API bearer token) first.", file=sys.stderr)
        sys.exit(1)
    logger.info("Starting x-basic-stdio over stdio (token from %s)", TOKEN_ENV)
    mcp.run()  # stdio is the default transport
