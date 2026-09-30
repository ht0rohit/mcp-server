# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo. Read it before changing anything.

## What this repo is

A learning repo for building MCP (Model Context Protocol) servers in Python the right way, as a
series that goes from a basic stdio server to advanced Streamable HTTP servers. The plan is in
`docs/roadmap.md`, and the practices each server teaches are in `docs/learnings.md`.

## Branches: the one rule that matters

| Branch | Holds | How it changes |
|---|---|---|
| `main` | Only the shared files: this file, `README.md`, `docs/`, CI, the starter `pyproject.toml` and `uv.lock`, `.gitignore`, `.python-version`. **Never a server.** | Commit and push directly to `main`. No extra branch, no PR. |
| `mcp/<nn>-<service>-<flavour>` | Exactly one server, at the repo root (`src/<package>/`, `tests/`, its own `pyproject.toml` and `README.md`), on top of `main`. | Created once from the latest `main`. After that it changes only through PRs from its work branches. **Never merge it into `main` and never open a PR into `main`.** |
| `work/<nn>-<service>-<flavour>-<topic>` | One piece of work on a server (a step, a fix, review feedback). | Cut from its server branch, push every commit to it, open a PR **into that server branch**, and delete it (remote and local) once the PR is merged. |

- **Work branches.** Change a server through one `work/...` branch per piece of work, not one
  per commit. Its PR's base is always the server branch it was cut from: check the base before
  creating the PR, because GitHub and the Claude app default to `main`. The owner merges
  ("Create a merge commit", so each commit stays visible), then the work branch is deleted and
  the next piece of work starts from the updated server branch.
- **A PR into `main` from a server or work branch must never exist, even open.** If one
  appears (for example from a "Create PR" button), close it without merging and say so.
- The prefix is `work/`, not `mcp/<server>/...`: git cannot hold `mcp/03-x` and `mcp/03-x/y`
  as branches at the same time.
- `<nn>` is the next free two-digit number (`01`, `02`, ...). Example: `mcp/01-x-basic-stdio`.
- A new server can start from an existing server branch when it builds on it (for example an
  advanced X server cut from `mcp/01-x-basic-stdio`). It still gets its own branch, and replaces
  the old server's package, tests and project settings with its own.
- After pushing a server branch, update `main` in one small direct commit: add the branch to the
  README index, set its row in `docs/roadmap.md`, and add new lessons to `docs/learnings.md`.
- Each server has its own `.claude/CLAUDE.md` on its branch, for guidance that only applies to
  that server (its files, upstream API quirks, how to extend it, what it deliberately leaves
  out). Read it before changing the server. This root file stays the same on every branch.
- To bring updated shared files from `main` onto a server branch, copy them rather than merging:
  `git checkout origin/main -- CLAUDE.md docs/ .github/ .gitignore` then commit. (Merging `main`
  into `mcp/01-x-basic-stdio` would apply an old revert and delete the server.) `README.md`,
  `pyproject.toml` and `uv.lock` are the server's own on its branch, so never copy those.

## Before calling a server done

Run the review checklist in `docs/adding-a-server.md` (step 4) and confirm CI ran on the pushed
branch. Green local tests are not enough: the review of servers 01 and 02 found real bugs that
every test passed over. When you decide something on the user's behalf, add one line to
`docs/decisions.md`.

## Commits

- Author every commit as the repo owner: `Rohit Kr Singh <ht97kumarrk@gmail.com>`.
- No `Co-Authored-By` trailers and no "Generated with ..." lines in commits, PRs or comments.

## Layout

```
main
├── CLAUDE.md
├── README.md                 # overview and the index of server branches
├── pyproject.toml            # starter: shared dev tools, pytest and ruff config, no server
├── uv.lock
├── .github/workflows/ci.yml  # lint + tests for the server on the branch
└── docs/
    ├── roadmap.md            # the planned series, basic -> advanced, with status
    ├── adding-a-server.md    # step-by-step for a new server branch
    ├── learnings.md          # best practices, grouped by the server that teaches them
    └── decisions.md          # decisions made on the user's behalf, one line each

a server branch keeps CLAUDE.md, docs/, CI, .gitignore and .python-version, and has at the root
├── .claude/CLAUDE.md         # guidance for this server only
├── pyproject.toml            # the server's deps, console script and the shared dev config
├── uv.lock
├── README.md                 # what it exposes, how to run it, how to connect a client
├── .env.example              # every env var it reads, no real values (.env is git-ignored)
├── src/<package>/            # server.py (MCP), client.py (upstream API), formatting.py, ...
└── tests/                    # no network, no secrets
```

## Commands

```bash
uv sync                                   # install the branch's server + dev tools
uv run pytest                             # its tests (on main: "no tests ran", exit 5, expected)
uv run ruff check . && uv run ruff format .
uv run <script>                           # run the server (its README names the script)
npx @modelcontextprotocol/inspector uv run <script>   # try it in a browser
```

Switching branches can leave ignored `__pycache__` folders under `src/` and `tests/`; they are
harmless, and `git clean -fdX src/ tests/` removes them. Don't run `git clean -fdX` on the
whole repo: it would also delete your `.env` and `.venv`.

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
