# CLAUDE.md: x-advanced-http

Guidance for this server only. The repo-wide rules (branches, commits, MCP conventions) are in
the root `CLAUDE.md`.

## What this server is for

Server 02 in the series: the X server from `mcp/01-x-basic-stdio`, taken end to end over
**Streamable HTTP**. It shows what a server needs once it leaves your laptop (auth, typed
results, progress, confirmation before side effects, change notifications, completions) while
staying diffable against server 01. Keep the read tools behaving like 01's.

## Scope

In scope: Streamable HTTP, static bearer-token auth with scopes, structured output, progress,
completions, elicitation through `Resolve`/`Elicit`, `subscriptions/listen` notifications,
middleware, retries, an app factory, env-based config.

Out of scope (later servers): OAuth with a real authorization server, OpenTelemetry exporters,
a shared `SubscriptionBus` across replicas, sampling, caching hints, Docker/deploy files. The
MCP logging capability is deprecated in 2026-07-28: log with the standard `logging` module.

## Files

| File | Holds | Rule |
|---|---|---|
| `src/x_advanced_http/server.py` | Tools, resources, prompts, completions, middleware, `create_server`, `create_app`, `main` | Numbered sections, read top to bottom. Nothing here reads the environment except `main()`/`create_app()` via `Config.from_env()` |
| `src/x_advanced_http/config.py` | `Config` and its parsers | Every env var is listed in `.env.example`; bad values raise `ConfigError` naming the variable |
| `src/x_advanced_http/auth.py` | `StaticTokenVerifier`, `require_scope()` | Compare tokens in constant time; never log a token |
| `src/x_advanced_http/client.py` | `XClient`, `UserCache`, HTTP error mapping | No MCP imports except `ToolError`. Only GET and DELETE are retried |
| `src/x_advanced_http/models.py` | Pydantic result models and X JSON converters | A tool's return annotation is its `outputSchema`: changing a model changes the contract |
| `tests/conftest.py` | `FakeX`, `config`, in-process `client`, `http` and `connect` (HTTP) fixtures | Mirror real X behaviour |

## How tools are registered

The server only exists once a `Config` does, so tools are module-level functions marked with the
local `@tool(title, annotations, writes=...)` decorator, which appends to `TOOLS`.
`create_server()` registers them with `mcp.add_tool(...)` and skips `writes=True` tools when
there is no `X_USER_ACCESS_TOKEN`. Resources, prompts and the completion handler are registered
in `create_server()` too.

## Adding a tool

1. Add the endpoint to `XClient`. Reuse `post_params()` for post endpoints.
2. Add a result model to `models.py` if no existing one fits.
3. Add the tool in `server.py` with `@tool(...)`: name `x_<verb>_<noun>`, a docstring that says
   what it returns, flat `Annotated` arguments with the endpoint's real limits, and the right
   annotations (`READ_ONLY`, `PUBLISHES` or `DESTROYS`).
4. A tool with side effects takes `x: Annotated[XClient, Resolve(writer)]` (scope check) and a
   confirmation resolver that itself depends on `writer`, so the scope is checked before the
   user is asked. Build the question only from the tool's arguments: on 2026-07-28 the call is
   retried and the answer is matched to the exact question text.
5. Teach `FakeX` the endpoint and test the happy path, errors and validation. Update
   `READ_TOOLS`/`WRITE_TOOLS` in `test_server.py`, and the README tables.

## Things that are easy to get wrong

- Static resources (no `{params}` in the URI) get no `Context`. `x://me/posts` reads the
  lifespan state through the `running` dict that `create_server()` closes over.
- The completion handler also gets no `Context`; it closes over the same `UserCache`.
- Auth runs before the Host check: an unauthenticated request with a bad Host gets 401, not 421.
- `httpx2.ASGITransport` buffers responses, so legacy (2025-11-25) sessions hang against the
  in-process app. Test legacy clients in-memory (`Client(server, mode="legacy")`) or against the
  real process (`test_process.py`).
- Progress must only increase, and `report_progress` is a no-op when the client didn't ask.

## X API facts the code relies on

Everything in server 01's CLAUDE.md, plus:

- Writes need an OAuth 2.0 user access token (`tweet.write`); the app-only bearer token is
  read-only. `POST /2/tweets` returns `201` with `data.id`; `DELETE /2/tweets/:id` returns
  `data.deleted`.
- `GET /2/users/me` needs the user token.
- Recent search pages hold at most 100 posts; the digest asks for at least 10 per page.

## Commands

```bash
uv run pytest servers/x-advanced-http
X_BEARER_TOKEN=... MCP_AUTH_TOKENS='<token>=x:read,x:write' uv run --package x-advanced-http x-advanced-http
uvicorn --factory x_advanced_http.server:create_app --port 8000   # same app, env config
```
