# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo.

## What this repo is

A learning repo for building MCP (Model Context Protocol) servers in Python the right way.
Each server is a small, self-contained package under `servers/`, and the series goes from a
basic stdio server to advanced HTTP servers. See `docs/roadmap.md` for the order.

## Layout

```
.
├── CLAUDE.md                 # this file
├── README.md                 # overview and server index
├── pyproject.toml            # uv workspace root: shared dev tools, pytest and ruff config
├── uv.lock                   # one lock file for the whole workspace (commit it)
├── .python-version
├── docs/
│   ├── roadmap.md            # the planned server series, basic -> advanced
│   ├── adding-a-server.md    # checklist for starting a new server
│   └── learnings.md          # best practices, one line per lesson, with the server that shows it
└── servers/
    └── <server-name>/        # one uv workspace member per server
        ├── pyproject.toml    # its own deps and console script
        ├── README.md         # what it does, how to run it, how to connect a client
        ├── .env.example      # every env var it reads, no real values
        ├── src/<package>/    # server code (src layout)
        └── tests/            # no network, no secrets
```

## Workflow

- `main` holds shared files and finished servers. Every new server starts on its own branch cut
  from the latest `main`: `mcp/<nn>-<service>-<flavour>` (for example `mcp/01-x-basic-stdio`),
  then goes to `main` through a pull request.
- Server folders are `servers/<service>-<flavour>` (for example `servers/x-basic-stdio`), and the
  Python package is the same name with underscores.
- Commits and PRs are authored by the repo owner only. Do not add `Co-Authored-By` trailers or
  "Generated with" lines.

## Commands

```bash
uv sync --all-packages                 # install every server + dev tools
uv run pytest                          # all tests
uv run pytest servers/<name>           # one server
uv run ruff check . && uv run ruff format .
uv run --package <name> <script>       # run a server (its README names the script)
npx @modelcontextprotocol/inspector uv run --package <name> <script>   # try it in a browser
```

## Conventions for MCP servers

- SDK: the official `mcp` package, v2 (`from mcp.server.mcpserver import MCPServer`).
  `FastMCP` / `mcp.server.fastmcp` is the v1 name and no longer exists. Pin `mcp>=2.2,<3`.
- stdio servers never write to stdout except through the protocol. Log to stderr.
- Tool names are prefixed and verb-first (`x_get_user`), have a docstring the model can act on,
  use flat, annotated arguments (`Annotated[int, Field(ge=5, le=100)]`) and set
  `ToolAnnotations` (`read_only_hint`, `open_world_hint`, ...).
- Expected failures raise `ToolError` (the client gets `is_error: true` plus the message). Never
  return an "Error: ..." string as if it succeeded.
- Shared resources (HTTP clients, DB pools) are created once in the server `lifespan` and read
  from `ctx.request_context.lifespan_context`.
- Secrets come from environment variables, are listed in `.env.example`, and are never committed.
- Tests use the in-process `mcp.Client(server)`, mock HTTP with `httpx.MockTransport`, and run
  async tests with the anyio plugin (`pytest.mark.anyio`), not pytest-asyncio. Each stdio server
  also has one smoke test that spawns it as a real subprocess.
- When a server teaches something new, add a line to `docs/learnings.md` and tick it off in
  `docs/roadmap.md`.
