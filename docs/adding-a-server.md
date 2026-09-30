# Adding a server

Each server lives on its own branch and is never merged into `main`. See the branch rule in
[CLAUDE.md](../CLAUDE.md).

## 1. Cut the branch

```bash
git switch main && git pull && git clean -fdX servers/
git switch -c mcp/<nn>-<service>-<flavour>          # <nn> = next free number
```

To build on an earlier server, cut from its branch instead of `main`.

## 2. Create the package

```bash
uv init --package --name <service>-<flavour> servers/<service>-<flavour>
uv add --package <service>-<flavour> "mcp>=2.2,<3"
```

Shape it like the earlier servers: `src/<package>/server.py` with a `main()` that the
`[project.scripts]` entry points to, a `README.md`, a `CLAUDE.md` with guidance for this server
only, a `.env.example` and a `tests/` folder.

## 3. Test without network or secrets

- In-process through the protocol: `async with Client(server) as client: ...`
- Upstream HTTP: `httpx.MockTransport`
- One smoke test that spawns the real process (stdio) or starts the app (HTTP)

```bash
uv sync --all-packages && uv run ruff check . && uv run ruff format --check . && uv run pytest
```

## 4. Push the branch

```bash
git push -u origin mcp/<nn>-<service>-<flavour>
```

No PR into `main`: the branch is the deliverable.

## 5. Update the index on `main`

```bash
git switch main && git clean -fdX servers/
# README.md: add a row to the server index
# docs/roadmap.md: set the server's status and branch
# docs/learnings.md: add what the server teaches
git commit -am "Index mcp/<nn>-<service>-<flavour>" && git push
```

## stdio or Streamable HTTP?

| | stdio | Streamable HTTP |
|---|---|---|
| Who starts it | The client launches it as a subprocess | You run it; clients connect to a URL |
| Good for | Local tools, local files, secrets on your machine | Shared or remote servers, web clients |
| Never | `print()` to stdout: stdout is the protocol | Expose it without auth or bind `0.0.0.0` casually |
| Auth | Env vars set in the client config | Bearer token or OAuth on the endpoint |
| Run | `mcp.run()` | `mcp.run(transport="streamable-http")` |

## What can be verified where

Unit tests, the protocol smoke test and the Inspector against a local process work in a cloud
session or on your machine. Real clients (Claude Desktop, Claude Code), local files and live API
calls with your token need your machine. Say which ones you checked in the server README.
