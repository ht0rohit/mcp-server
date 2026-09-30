# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo. Read it before changing anything.

## What this repo is

A learning repo for building MCP (Model Context Protocol) servers in Python the right way, as a
series that goes from a basic stdio server to advanced Streamable HTTP servers. The plan is in
`docs/roadmap.md`, and the practices each server teaches are in `docs/learnings.md`.

## Branches: the one rule that matters

| Branch | Holds | How it changes |
|---|---|---|
| `main` | Only the shared files: this file, `README.md`, `docs/`, CI, `pyproject.toml`, `uv.lock`, `.gitignore`, `.python-version`, `servers/.gitkeep`. **Never a server.** | Commit and push directly to `main`. No extra branch, no PR. |
| `mcp/<nn>-<service>-<flavour>` | Exactly one server, in `servers/<service>-<flavour>/`, on top of `main`. | Cut from the latest `main`, push it, and leave it there. **Never merge it into `main` and never open a PR into `main`.** |

- `<nn>` is the next free two-digit number (`01`, `02`, ...). Example: `mcp/01-x-basic-stdio`.
- A new server can start from an existing server branch when it builds on it (for example an
  advanced X server cut from `mcp/01-x-basic-stdio`). It still gets its own branch and folder.
- After pushing a server branch, update `main` in one small direct commit: add the branch to the
  README index, set its row in `docs/roadmap.md`, and add new lessons to `docs/learnings.md`.
- Each server has its own `servers/<service>-<flavour>/CLAUDE.md` on its branch, for guidance
  that only applies to that server (its files, upstream API quirks, how to extend it, what it
  deliberately leaves out). This root file stays the same on every branch.
- To bring updated shared docs from `main` onto a server branch, copy them rather than merging:
  `git checkout origin/main -- CLAUDE.md docs/` then commit. (Merging `main` into
  `mcp/01-x-basic-stdio` would apply an old revert and delete the server.)

## Commits

- Author every commit as the repo owner: `Rohit Kr Singh <ht97kumarrk@gmail.com>`.
- No `Co-Authored-By` trailers and no "Generated with ..." lines in commits, PRs or comments.

## Layout

```
main
├── CLAUDE.md
├── README.md                 # overview and the index of server branches
├── pyproject.toml            # uv workspace root: shared dev tools, pytest and ruff config
├── uv.lock
├── .github/workflows/ci.yml  # lint + tests for whatever servers the branch contains
├── docs/
│   ├── roadmap.md            # the planned series, basic -> advanced, with status
│   ├── adding-a-server.md    # step-by-step for a new server branch
│   └── learnings.md          # best practices, grouped by the server that teaches them
└── servers/.gitkeep

a server branch adds
└── servers/<service>-<flavour>/
    ├── CLAUDE.md             # guidance for this server only
    ├── pyproject.toml        # its deps and console script (a uv workspace member)
    ├── README.md             # what it exposes, how to run it, how to connect a client
    ├── .env.example          # every env var it reads, no real values
    ├── src/<package>/        # server.py (MCP), client.py (upstream API), formatting.py, ...
    └── tests/                # no network, no secrets
```

## Commands

```bash
uv sync --all-packages                    # install the branch's servers + dev tools
uv run pytest                             # all tests (on main: "no tests ran", exit 5, expected)
uv run ruff check . && uv run ruff format .
uv run --package <name> <script>          # run a server (its README names the script)
npx @modelcontextprotocol/inspector uv run --package <name> <script>   # try it in a browser
git clean -fdX servers/                   # after switching back to main (see below)
```

Switching from a server branch to `main` can leave ignored files such as `__pycache__` in
`servers/<name>/`. uv then fails with "workspace member is missing a pyproject.toml";
`git clean -fdX servers/` fixes it.

## Conventions for MCP servers

- SDK: the official `mcp` package, v2 (`from mcp.server.mcpserver import MCPServer`). `FastMCP`
  and `mcp.server.fastmcp` are the v1 names and no longer exist. Pin `mcp>=2.2,<3`.
- stdio servers never write to stdout except through the protocol. Log to stderr.
- Tool names are prefixed and verb-first (`x_get_user`), with a docstring the model can act on,
  flat annotated arguments (`Annotated[int, Field(ge=5, le=100)]`) and `ToolAnnotations`.
- Expected failures raise `ToolError` in tools and `ResourceError` or `ResourceNotFoundError` in
  resources. Never return an "Error: ..." string as if the call succeeded.
- Shared clients (HTTP, DB) are opened once in the server `lifespan` and read from
  `ctx.request_context.lifespan_context`.
- Secrets come from environment variables, are listed in `.env.example`, and are never committed.
- Tests use the in-process `mcp.Client(server)`, mock HTTP with `httpx.MockTransport`, and run
  async tests with the anyio plugin (`pytest.mark.anyio`), not pytest-asyncio. Each server also
  has one smoke test against the real process (stdio) or app (HTTP).
- Each server README says what was verified in a cloud session and what still needs a local
  machine (real clients, live API calls with a token).
