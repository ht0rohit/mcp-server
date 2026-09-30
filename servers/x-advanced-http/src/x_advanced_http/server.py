"""x-advanced-http: an X (Twitter) MCP server over Streamable HTTP.

Server 01 (`x-basic-stdio`) covered the building blocks. This one keeps its read tools and adds
what a server needs once it leaves your laptop:

1. An app factory: `create_server(config)` builds a fully configured server, so tests and
   production differ only in the `Config` they pass.
2. Streamable HTTP, a health route and a Host allowlist.                 -> `create_app()`
3. Bearer-token auth, with per-tool scopes.                              -> `auth.py`, `writer()`
4. Structured output: every tool returns a Pydantic model.               -> `models.py`
5. Progress notifications from a long-running tool.                      -> `x_search_digest`
6. Write tools that ask the user first, through `Resolve` + `Elicit`.    -> `x_create_post`
7. Change notifications: writes tell subscribers x://me/posts changed.   -> `notify_...()`
8. Completions for prompt arguments and resource template parameters.   -> `complete()`
9. Middleware that logs every request with its caller and duration.      -> `log_requests()`

Read it top to bottom: shared state, tools, resources, prompts, completions, then the factory
that wires them together and `main()`.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import Context, Elicit, MCPServer, RequestStateSecurity, Resolve
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
    ToolAnnotations,
)
from pydantic import AnyHttpUrl, BaseModel, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import StaticTokenVerifier, require_scope
from .client import UserCache, XApiError, XClient, XNotFoundError
from .config import READ_SCOPE, WRITE_SCOPE, Config, ConfigError
from .models import (
    AuthorCount,
    Post,
    PostPage,
    PostResult,
    Posts,
    SearchDigest,
    User,
    Users,
    authors_by_id,
    missing_items,
    post_page,
)

logger = logging.getLogger(__name__)

NAME = "x-advanced-http"
VERSION = "0.1.0"
MY_POSTS_URI = "x://me/posts"


# --- 1. Shared state, opened once for the life of the server ------------------------------
#
# Over Streamable HTTP the lifespan runs once at startup and its state is shared by every
# session and request, so this is the place for connection pools and caches.


@dataclass
class AppContext:
    x: XClient  # app-only token: reads
    x_user: XClient | None  # user-context token: writes and "me"; None if not configured
    users: UserCache
    me_id: str | None = None  # the signed-in account's ID, looked up once


def app_context(ctx: Context) -> AppContext:
    return ctx.request_context.lifespan_context


# Tests replace this to route X traffic to a fake API.
def make_transport() -> httpx.AsyncBaseTransport | None:
    return None


async def lookup_user(ctx: Context, username: str) -> dict[str, Any]:
    """Resolve a handle to X's user JSON, through the cache."""
    state = app_context(ctx)
    handle = username.removeprefix("@")
    user = state.users.get(handle)
    if user is None:
        user = (await state.x.user_by_username(handle))["data"]
        state.users.put(user)
    return user


# --- 2. Tools -----------------------------------------------------------------------------
#
# What's new since server 01:
# - Return types are Pydantic models, so each tool publishes an `outputSchema` and returns
#   `structured_content` alongside the JSON text the model reads.
# - Tools are plain module-level functions collected in `TOOLS` and registered by the factory,
#   because the server object only exists once a `Config` does.
# - Write tools depend on resolvers (`Resolve(...)`): one checks the caller's scope, the next
#   asks the user to confirm. The model can't supply or skip either.

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
PUBLISHES = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
DESTROYS = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True
)

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
    str | None, Field(description="next_cursor from a previous call, to get the next page")
]
Query = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        description="X search query, e.g. 'from:XDevelopers -is:retweet' or '\"mcp\" lang:en'",
    ),
]


@dataclass(frozen=True)
class ToolSpec:
    fn: Callable[..., Any]
    title: str
    annotations: ToolAnnotations
    writes: bool = False


TOOLS: list[ToolSpec] = []


def tool(title: str, annotations: ToolAnnotations, *, writes: bool = False):
    """Collect a tool for `create_server` to register."""

    def decorator(fn):
        TOOLS.append(ToolSpec(fn, title, annotations, writes))
        return fn

    return decorator


@tool("Get X user", READ_ONLY)
async def x_get_user(username: Username, ctx: Context) -> User:
    """Get one X user's profile: name, ID, bio, location, join date and follower counts."""
    return User.from_x(await lookup_user(ctx, username))


@tool("Get several X users", READ_ONLY)
async def x_get_users(
    usernames: Annotated[list[Username], Field(min_length=1, max_length=100)], ctx: Context
) -> Users:
    """Get up to 100 X user profiles in one call. Prefer this over repeated x_get_user calls."""
    state = app_context(ctx)
    body = await state.x.users_by_usernames([u.removeprefix("@") for u in usernames])
    if not body.get("data"):
        raise XApiError("None of those X users exist.")
    for user in body["data"]:
        state.users.put(user)
    return Users(users=[User.from_x(u) for u in body["data"]], not_found=missing_items(body))


@tool("Get X post", READ_ONLY)
async def x_get_post(post_id: PostId, ctx: Context) -> Post:
    """Get one X post by ID: author, time, full text, language and engagement counts."""
    body = await app_context(ctx).x.post(post_id)
    return Post.from_x(body["data"], authors_by_id(body))


@tool("Get several X posts", READ_ONLY)
async def x_get_posts(
    post_ids: Annotated[list[PostId], Field(min_length=1, max_length=100)], ctx: Context
) -> Posts:
    """Get up to 100 X posts by ID in one call."""
    body = await app_context(ctx).x.posts(post_ids)
    if not body.get("data"):
        raise XApiError("None of those X posts exist (deleted, private or wrong IDs).")
    return Posts(posts=post_page(body).posts, not_found=missing_items(body))


@tool("List a user's posts", READ_ONLY)
async def x_get_user_posts(
    username: Username,
    ctx: Context,
    max_results: Annotated[int, Field(ge=5, le=100, description="Posts per page (5-100)")] = 10,
    include_replies: bool = False,
    include_reposts: bool = False,
    cursor: Cursor = None,
) -> PostPage:
    """List an X user's most recent posts, newest first. Returns next_cursor when there are more."""
    user = await lookup_user(ctx, username)
    excluded = [
        name
        for name, include in (("replies", include_replies), ("retweets", include_reposts))
        if not include
    ]
    body = await app_context(ctx).x.user_posts(
        user["id"], max_results, cursor, ",".join(excluded) or None
    )
    return post_page(body)


@tool("List a user's mentions", READ_ONLY)
async def x_get_user_mentions(
    username: Username,
    ctx: Context,
    max_results: Annotated[int, Field(ge=5, le=100, description="Posts per page (5-100)")] = 10,
    cursor: Cursor = None,
) -> PostPage:
    """List recent posts that mention an X user, newest first. Returns next_cursor for more."""
    user = await lookup_user(ctx, username)
    return post_page(await app_context(ctx).x.user_mentions(user["id"], max_results, cursor))


@tool("Search recent X posts", READ_ONLY)
async def x_search_recent_posts(
    query: Query,
    ctx: Context,
    max_results: Annotated[int, Field(ge=10, le=100, description="Posts per page (10-100)")] = 10,
    sort_order: Literal["recency", "relevancy"] = "recency",
    cursor: Cursor = None,
) -> PostPage:
    """Search X posts from the last 7 days. See x://guides/search-operators for query syntax."""
    body = await app_context(ctx).x.search_recent(query, max_results, cursor, sort_order)
    return post_page(body)


@tool("Digest a search", READ_ONLY)
async def x_search_digest(
    query: Query,
    ctx: Context,
    max_posts: Annotated[
        int, Field(ge=10, le=500, description="Stop after this many posts (10-500)")
    ] = 200,
) -> SearchDigest:
    """Scan up to 500 recent posts matching a query and summarize them: the most engaging posts,
    the most active authors and the languages used. Use this instead of paging through
    x_search_recent_posts yourself when you want the overall picture."""
    x = app_context(ctx).x
    posts: list[Post] = []
    pages, cursor = 0, None
    while len(posts) < max_posts:
        page_size = min(100, max(10, max_posts - len(posts)))
        page = post_page(await x.search_recent(query, page_size, cursor, "recency"))
        pages += 1
        posts.extend(page.posts[: max_posts - len(posts)])
        cursor = page.next_cursor
        # Progress must only go up. It is a no-op unless the client asked for it, so report
        # unconditionally.
        await ctx.report_progress(len(posts), max_posts, f"Fetched page {pages}")
        if not cursor or not page.posts:
            break

    authors = Counter(p.author for p in posts)
    return SearchDigest(
        query=query,
        posts_scanned=len(posts),
        pages_fetched=pages,
        complete=cursor is None,
        top_posts=sorted(posts, key=lambda p: p.engagement, reverse=True)[:5],
        top_authors=[AuthorCount(username=a, posts=n) for a, n in authors.most_common(5)],
        languages=dict(Counter(p.lang or "und" for p in posts).most_common()),
    )


# Write tools. Each takes its X client from `writer`, which refuses callers without the
# x:write scope; then a confirmation resolver asks the user. Order matters: the scope check
# runs first, so a caller who may not post is never shown a confirmation prompt.


async def writer(ctx: Context) -> XClient:
    require_scope(WRITE_SCOPE)
    x_user = app_context(ctx).x_user
    if x_user is None:  # the factory only registers write tools when a user token exists
        raise ToolError("Posting is not configured on this server (no X_USER_ACCESS_TOKEN).")
    return x_user


class Confirmation(BaseModel):
    confirm: bool = Field(description="Go ahead?")


async def confirm_create(
    text: str, reply_to_post_id: str | None, _x: Annotated[XClient, Resolve(writer)]
) -> Elicit[Confirmation]:
    # The question must be built only from the arguments: on the 2026-07-28 protocol the call
    # is retried after the answer, and the answer is matched back to this exact question.
    where = f" as a reply to post {reply_to_post_id}" if reply_to_post_id else ""
    return Elicit(f"Publish this post on X{where}?\n\n{text}", Confirmation)


async def confirm_delete(
    post_id: str, _x: Annotated[XClient, Resolve(writer)]
) -> Elicit[Confirmation]:
    return Elicit(f"Delete X post {post_id}? This cannot be undone.", Confirmation)


@tool("Publish an X post", PUBLISHES, writes=True)
async def x_create_post(
    text: Annotated[
        str,
        Field(
            min_length=1,
            max_length=25_000,
            description=(
                "Post text. X allows 280 weighted characters (URLs count 23, emoji and CJK 2) "
                "or more on Premium; X itself rejects a post that is too long."
            ),
        ),
    ],
    ctx: Context,
    x: Annotated[XClient, Resolve(writer)],
    confirmation: Annotated[Confirmation, Resolve(confirm_create)],
    reply_to_post_id: Annotated[
        str | None, Field(pattern=r"^\d{1,19}$", description="Post ID to reply to")
    ] = None,
) -> PostResult:
    """Publish a post (or a reply) on the signed-in X account. The user is asked to confirm the
    exact text first; if they decline, nothing is posted."""
    if not confirmation.confirm:
        return PostResult(status="cancelled", text=text)
    body = await x.create_post(text, reply_to_post_id)
    post_id = body["data"]["id"]
    await ctx.notify_resource_updated(MY_POSTS_URI)
    return PostResult(
        status="posted", post_id=post_id, text=text, url=f"https://x.com/i/status/{post_id}"
    )


@tool("Delete an X post", DESTROYS, writes=True)
async def x_delete_post(
    post_id: PostId,
    ctx: Context,
    x: Annotated[XClient, Resolve(writer)],
    confirmation: Annotated[Confirmation, Resolve(confirm_delete)],
) -> PostResult:
    """Delete one of the signed-in account's posts. The user is asked to confirm first."""
    if not confirmation.confirm:
        return PostResult(status="cancelled", post_id=post_id)
    body = await x.delete_post(post_id)
    if not body.get("data", {}).get("deleted"):
        # A retried DELETE sees `deleted: false` when the first attempt already worked but its
        # response was lost. If the post is gone, the delete succeeded.
        try:
            await app_context(ctx).x.post(post_id)
        except XNotFoundError:
            pass
        else:
            raise XApiError(f"X did not delete post {post_id}.")
    await ctx.notify_resource_updated(MY_POSTS_URI)
    return PostResult(status="deleted", post_id=post_id)


# --- 3. Resources -------------------------------------------------------------------------

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


def search_operators() -> str:
    return SEARCH_GUIDE


async def user_profile(username: str, ctx: Context) -> str:
    try:
        user = await lookup_user(ctx, username)
    except XNotFoundError as exc:
        raise ResourceNotFoundError(str(exc)) from exc
    except XApiError as exc:
        # Rate limits, auth and network failures are not "not found".
        raise ResourceError(str(exc)) from exc
    return User.from_x(user).model_dump_json(indent=2)


async def read_my_posts(state: AppContext) -> str:
    """The signed-in account's latest posts, as JSON."""
    assert state.x_user is not None  # only registered when a user token exists
    if state.me_id is None:
        state.me_id = (await state.x_user.me())["data"]["id"]
    body = await state.x_user.user_posts(state.me_id, 10, None, None)
    return post_page(body).model_dump_json(indent=2)


# --- 4. Prompts ---------------------------------------------------------------------------


def profile_brief(username: str) -> str:
    """Summarize who an X user is and what they have been posting about lately."""
    handle = username.removeprefix("@")
    return (
        f"Write a short brief on the X user @{handle}.\n"
        f"1. Use x_get_user for their profile.\n"
        f"2. Use x_get_user_posts with max_results 20 for their recent posts.\n"
        "Then summarize in under 200 words: who they are, the main topics they post about, "
        "and their most engaging recent post (with its URL)."
    )


def topic_pulse(topic: str, language: str = "en") -> str:
    """See what people on X are saying about a topic this week."""
    return (
        f"Find out what people on X are saying about {topic!r} this week.\n"
        f"Read x://guides/search-operators, then call x_search_digest with a query that targets "
        f"the topic, adds lang:{language} and excludes reposts.\n"
        "Report the 3 to 5 main themes, the overall tone, the most active authors, and link 3 "
        "representative posts."
    )


# --- 5. Completions -----------------------------------------------------------------------
#
# One handler serves every prompt argument and resource-template parameter. It has no
# `Context`, so the factory hands it the same `UserCache` the lifespan uses.

LANGUAGES = ["ar", "de", "en", "es", "fr", "hi", "it", "ja", "ko", "nl", "pt", "ru", "tr", "zh"]


def make_completer(users: UserCache):
    async def complete(
        ref: PromptReference | ResourceTemplateReference,
        argument: CompletionArgument,
        context: CompletionContext | None,
    ) -> Completion | None:
        if argument.name == "language":
            return Completion(values=[c for c in LANGUAGES if c.startswith(argument.value)])
        if argument.name == "username":
            # Handles this server has looked up recently. The SDK does not filter: we do.
            return Completion(values=users.handles(argument.value)[:100])
        return None

    return complete


# --- 6. Middleware ------------------------------------------------------------------------


async def log_requests(ctx: ServerRequestContext, call_next: CallNext) -> HandlerResult:
    """Log every MCP message with who sent it and how long it took (never the token)."""
    start = time.perf_counter()
    outcome = "error"
    try:
        result = await call_next(ctx)
        outcome = "ok"
        return result
    finally:
        token = get_access_token()
        logger.info(
            "%s %s by %s in %.1f ms",
            ctx.method,
            outcome,
            token.client_id if token else "local",
            (time.perf_counter() - start) * 1000,
        )


# --- 7. The factory -----------------------------------------------------------------------

INSTRUCTIONS = (
    "Access to X (Twitter): look up users and posts, list a user's posts and mentions, "
    "search the last 7 "
    "days, and digest a search into top posts and authors. Usernames may include '@'. "
    "List tools return next_cursor when more results exist; pass it back as cursor. "
    "Read x://guides/search-operators before writing complex search queries."
)
WRITE_INSTRUCTIONS = (
    " This server can also publish and delete posts on the signed-in account; the user "
    "confirms each one. Never post without the user asking you to."
)


def create_server(config: Config) -> MCPServer:
    """Build the server for one configuration. Nothing here reads the environment."""
    users = UserCache()
    # Static resources (no URI parameters) get no Context, so the lifespan also publishes its
    # state here for them.
    running: dict[str, AppContext] = {}

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
        transport = make_transport()
        x = XClient(config.x_bearer_token, token_env="X_BEARER_TOKEN", transport=transport)
        x_user = None
        if config.x_user_access_token:
            x_user = XClient(
                config.x_user_access_token, token_env="X_USER_ACCESS_TOKEN", transport=transport
            )
        running["app"] = AppContext(x=x, x_user=x_user, users=users)
        try:
            yield running["app"]
        finally:
            running.clear()
            await x.aclose()
            if x_user is not None:
                await x_user.aclose()

    mcp = MCPServer(
        name=NAME,
        title="X",
        version=VERSION,
        instructions=INSTRUCTIONS + (WRITE_INSTRUCTIONS if config.can_write else ""),
        lifespan=lifespan,
        # Every request needs a valid token with x:read. Write tools check x:write themselves.
        token_verifier=StaticTokenVerifier(config.auth_tokens, resource=config.public_url),
        # The protected-resource metadata must name an authorization server, so it names this
        # server. That is a placeholder: nothing here issues tokens, and clients send a static
        # `Authorization: Bearer ...` header. A real authorization server replaces it later.
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.public_url),
            resource_server_url=AnyHttpUrl(config.public_url),
            required_scopes=[READ_SCOPE],
            validate_token_resource=True,
        ),
        # Confirmation round-trips carry sealed state. With shared keys, any worker can open
        # state another worker sealed. Without them each process makes its own key, which is
        # right for a single process.
        request_state_security=(
            RequestStateSecurity(keys=list(config.state_keys)) if config.state_keys else None
        ),
        middleware=[log_requests],
    )

    # Only expose what works: without a user token there are no write tools and no "me".
    for spec in TOOLS:
        if spec.writes and not config.can_write:
            continue
        mcp.add_tool(spec.fn, title=spec.title, annotations=spec.annotations)

    mcp.resource(
        "x://guides/search-operators",
        title="X search operators",
        description="Cheat sheet for writing search queries",
        mime_type="text/markdown",
    )(search_operators)
    mcp.resource(
        "x://users/{username}",
        title="X user profile",
        description="An X user's public profile as JSON",
        mime_type="application/json",
    )(user_profile)
    if config.can_write:

        async def my_posts() -> str:
            return await read_my_posts(running["app"])

        mcp.resource(
            MY_POSTS_URI,
            title="My recent X posts",
            description="The signed-in account's 10 latest posts; updated after each write",
            mime_type="application/json",
        )(my_posts)

    mcp.prompt(title="Profile brief")(profile_brief)
    mcp.prompt(title="Topic pulse")(topic_pulse)
    mcp.completion()(make_completer(users))

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> JSONResponse:
        # Custom routes skip auth: keep them to things anyone may know.
        return JSONResponse({"status": "ok", "server": NAME, "version": VERSION})

    return mcp


def create_app(config: Config | None = None) -> Starlette:
    """The ASGI app: MCP at /mcp, auth metadata under /.well-known, and /healthz.

    `uvicorn --factory x_advanced_http.server:create_app` serves it with settings from the
    environment, which is also how you would run several workers.
    """
    config = config or Config.from_env()
    mcp = create_server(config)
    security = TransportSecuritySettings(
        # DNS-rebinding protection: accept only these Host headers. Localhost always works.
        allowed_hosts=[
            f"127.0.0.1:{config.port}",
            f"localhost:{config.port}",
            f"[::1]:{config.port}",
            *config.allowed_hosts,
        ],
        allowed_origins=[f"http://127.0.0.1:{config.port}", f"http://localhost:{config.port}"],
    )
    return mcp.streamable_http_app(transport_security=security)


# --- 8. Run -------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog=NAME, description="X MCP server over Streamable HTTP")
    parser.add_argument("--host", help="Interface to bind (default: MCP_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port to listen on (default: MCP_PORT or 8000)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    try:
        # Flags go through from_env, so defaults derived from the port (the public URL) follow.
        config = Config.from_env(host=args.host, port=args.port)
    except ConfigError as exc:
        print(f"{NAME}: {exc}", file=sys.stderr)
        sys.exit(1)

    import uvicorn

    logger.info(
        "Starting %s on http://%s:%d/mcp (writes %s)",
        NAME,
        config.host,
        config.port,
        "enabled" if config.can_write else "disabled",
    )
    uvicorn.run(create_app(config), host=config.host, port=config.port, log_level="info")
