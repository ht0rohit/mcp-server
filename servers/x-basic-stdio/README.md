# x-basic-stdio

Server 01 in the series: a read-only MCP server for X (Twitter) over **stdio**, covering the basic
MCP building blocks: tools, resources, resource templates, prompts and a lifespan. It is built on
the official Python SDK v2 (`MCPServer`).

## What it exposes

**Tools** (all read-only, idempotent, open-world)

| Tool | X endpoint | Notes |
|---|---|---|
| `x_get_user` | `GET /2/users/by/username/:username` | Profile, bio, counts |
| `x_get_users` | `GET /2/users/by` | Up to 100 handles in one call; lists the ones not found |
| `x_get_post` | `GET /2/tweets/:id` | Text, author, time, engagement |
| `x_get_posts` | `GET /2/tweets` | Up to 100 IDs in one call |
| `x_get_user_posts` | `GET /2/users/:id/tweets` | Replies and reposts excluded unless asked; cursor pagination |
| `x_get_user_mentions` | `GET /2/users/:id/mentions` | Cursor pagination |
| `x_search_recent_posts` | `GET /2/tweets/search/recent` | Last 7 days, `recency` or `relevancy`; cursor pagination |

**Resources**

| URI | What |
|---|---|
| `x://guides/search-operators` | Markdown cheat sheet for search queries |
| `x://users/{username}` | Template: a user's profile as JSON |

**Prompts**

| Prompt | Arguments | What |
|---|---|---|
| `profile_brief` | `username` | Summarize who a user is and what they post about |
| `topic_pulse` | `topic`, `language` (default `en`) | Themes and tone of this week's posts on a topic |

## How the code is organized

```
src/x_basic_stdio/
├── server.py      # the MCP part: server, lifespan, tools, resources, prompts, main()
├── client.py      # async X API client and HTTP error -> ToolError mapping (no MCP here)
├── formatting.py  # X JSON -> compact text, size limit, pagination hint
└── __main__.py    # `python -m x_basic_stdio`
tests/
├── conftest.py    # fake X API (httpx.MockTransport) + in-process MCP client
├── test_server.py # tools, resources and prompts through the protocol
└── test_stdio.py  # spawns the real process over stdio
```

Start with `server.py`: it is written to be read top to bottom.

## Run it

You need an X API app-only **bearer token** (developer.x.com, your app, "Keys and tokens"). X's
free tier blocks most read endpoints, so expect `403` errors unless your plan includes them.

```bash
# from the repo root
uv sync --all-packages
X_BEARER_TOKEN=... uv run --package x-basic-stdio x-basic-stdio   # waits for a client on stdio
```

Poke at it in the browser with the MCP Inspector:

```bash
X_BEARER_TOKEN=... npx @modelcontextprotocol/inspector \
  uv run --package x-basic-stdio x-basic-stdio
```

### Claude Code

```bash
claude mcp add x-basic --env X_BEARER_TOKEN=... -- \
  uv run --directory /absolute/path/to/mcp-server --package x-basic-stdio x-basic-stdio
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "x-basic": {
      "command": "uv",
      "args": [
        "run", "--directory", "/absolute/path/to/mcp-server",
        "--package", "x-basic-stdio", "x-basic-stdio"
      ],
      "env": { "X_BEARER_TOKEN": "..." }
    }
  }
}
```

## Test it

```bash
uv run pytest servers/x-basic-stdio
```

The tests need no token or network: X is replaced by a fake that answers like the real API
(including `200` with an `errors` array for missing items, `403` and `429`).

## Verified

- Cloud session: unit tests through the protocol, and the stdio smoke test that spawns the server.
- Not yet verified: live X API calls with a real token, and Claude Desktop / Claude Code as the
  client. Both need your machine and token.

## What this server teaches

- `MCPServer` with `instructions` that tell the model how the tools fit together.
- One shared `httpx.AsyncClient` opened in the `lifespan`, read via
  `ctx.request_context.lifespan_context`, closed on shutdown.
- Flat, validated arguments (`Annotated[..., Field(...)]`): bad input is rejected before any
  request, and the limits show up in the input schema.
- `ToolAnnotations` so clients know the tools only read.
- Anticipated failures as `ToolError` (tools) and `ResourceNotFoundError` (resources).
- Cursor pagination and a size limit that drops whole items.
- stdio hygiene: logs go to stderr, and the server fails fast if the token is missing.
