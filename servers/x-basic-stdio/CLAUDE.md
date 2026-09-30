# CLAUDE.md: x-basic-stdio

Guidance for this server only. The repo-wide rules (branches, commits, MCP conventions) are in
the root `CLAUDE.md`.

## What this server is for

Server 01 in the series: the **basic** MCP building blocks over **stdio**, using a read-only X
(Twitter) API v2 client. It exists to be read and diffed against later servers, so keep it small
and plain.

## Scope: keep it basic

In scope: tools, static resources, resource templates, prompts, `instructions`, a lifespan,
`ToolAnnotations`, validated flat arguments, `ToolError` / `ResourceNotFoundError`, cursor
pagination, offline tests.

Out of scope here (they belong to later server branches): structured output (tools return plain
text with `structured_output=False`), progress, logging to the client, completions, elicitation,
sampling, HTTP transport, auth beyond a bearer token, any write endpoint (posting, liking,
following). If a change needs one of these, it goes in a new server branch.

## Files

| File | Holds | Rule |
|---|---|---|
| `src/x_basic_stdio/server.py` | The MCP layer: server, lifespan, tools, resources, prompts, `main()` | Written to be read top to bottom; keep the numbered sections and comments |
| `src/x_basic_stdio/client.py` | `XClient` (async httpx) and HTTP error to `XApiError` mapping | No MCP imports except `ToolError` |
| `src/x_basic_stdio/formatting.py` | X JSON to compact text, `CHARACTER_LIMIT`, pagination hint | Drop whole items, never cut text mid-item |
| `tests/conftest.py` | Fake X API (`fake_x_api`) and the in-process `client` fixture | Mirror real X behaviour |
| `tests/test_server.py` | Tools, resources, prompts through the protocol | |
| `tests/test_stdio.py` | Spawns `python -m x_basic_stdio` over stdio | Needs no token beyond a dummy |

## Adding a tool

1. Add the endpoint call to `XClient` in `client.py`, reusing `_post_params()` for post
   endpoints so authors come back via `expansions=author_id`.
2. Add the tool in `server.py`: name `x_<verb>_<noun>`, a docstring that says what it returns,
   `annotations=READ_ONLY`, `structured_output=False`, flat `Annotated` arguments with the
   endpoint's real limits, and `cursor: Cursor = None` if the endpoint paginates.
3. Teach `fake_x_api` in `tests/conftest.py` the endpoint, then test the happy path, the
   not-found path and argument validation. Update the tool count in `test_stdio.py` and the tool
   set in `test_server.py`.
4. Add the tool to the table in this server's `README.md`.

## X API facts the code relies on

- Base URL `https://api.x.com/2`, app-only bearer token from `X_BEARER_TOKEN`.
- A missing user or post is **HTTP 200** with an `errors` array and no `data`. Batch lookups
  return `data` for what exists plus `errors` for the rest.
- `max_results` ranges differ: user timelines and mentions 5 to 100, recent search 10 to 100.
- Pagination: timelines take `pagination_token`, recent search takes `next_token`; both return
  `meta.next_token`. Tools expose both as a single `cursor` argument.
- 403 usually means the API plan doesn't include the endpoint (the free tier blocks most reads);
  429 carries `x-rate-limit-reset`.

## Commands

```bash
uv run pytest servers/x-basic-stdio
X_BEARER_TOKEN=... uv run --package x-basic-stdio x-basic-stdio
X_BEARER_TOKEN=... npx @modelcontextprotocol/inspector uv run --package x-basic-stdio x-basic-stdio
```
