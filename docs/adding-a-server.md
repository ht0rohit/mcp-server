# Adding a server

Each server lives on its own branch and is never merged into `main`. See the branch rule in
[CLAUDE.md](../CLAUDE.md).

## 1. Cut the branch

```bash
git switch main && git pull
git switch -c mcp/<nn>-<service>-<flavour>          # <nn> = next free number
```

To build on an earlier server, cut from its branch instead of `main`, then remove that server's
`src/<package>/` and `tests/` once the new ones replace them.

## 2. Create the package

The server lives at the repo root. Turn the starter `pyproject.toml` into the server's project:
set `name` (`<service>-<flavour>`), `version` and `description`, delete `[tool.uv] package = false`,
and add the build backend and the console script:

```toml
[project.scripts]
<service>-<flavour> = "<package>:main"

[build-system]
requires = ["uv_build>=0.8.17,<0.9.0"]
build-backend = "uv_build"
```

```bash
uv add "mcp>=2.2,<3"
```

Shape it like the earlier servers: `src/<package>/server.py` with a `main()` that the
`[project.scripts]` entry points to, a `tests/` folder, a `.env.example`, a `.claude/CLAUDE.md`
with guidance for this server only, and a `README.md` for the server that replaces main's
index README on this branch.

## 3. Test without network or secrets

- In-process through the protocol: `async with Client(server) as client: ...`
- Upstream HTTP: `httpx.MockTransport`
- One smoke test that spawns the real process (stdio) or starts the app (HTTP)

```bash
uv sync && uv run ruff check . && uv run ruff format --check . && uv run pytest
```

## 4. Review before calling it done

Passing tests only prove the code matches its own assumptions. Before pushing, check the things
the review of servers 01 and 02 found that tests had missed:

- **Fakes vs the real API.** Compare the fake upstream in `tests/conftest.py` with the API docs,
  field by field, for every endpoint used. The fake only returns what its author expected (the
  X fake never returned `note_tweet`, so long posts were silently cut at 280 characters).
- **Parity with the server it builds on.** List the tools, resources and prompts of both and
  explain every difference in the new server's README or CLAUDE.md.
- **Every entry point.** Run each CLI flag and env var once and check the values derived from
  it (a `--port` flag left the public URL on the old port).
- **What the server advertises.** Read `/.well-known/*`, `initialize`, and `tools/list` output
  as a client would, and ask whether a real client could act on each field.
- **Retries against side effects.** For every retried call, ask what happens if the first
  attempt succeeded but its response was lost.
- **Error paths per surface.** Tools raise `ToolError`; resources raise
  `ResourceNotFoundError` only for "missing" and `ResourceError` for everything else.
- **Limits the upstream counts differently** (weighted characters, bytes vs code points).
- **CI actually ran** on the pushed branch (open the Actions tab), not just locally.

## 5. Push the branch

```bash
git push -u origin mcp/<nn>-<service>-<flavour>
```

No PR into `main`: the branch is the deliverable.

## Changing a server after that: work branches

Every later change to a server branch (a new step, a fix, review feedback) goes through a work
branch and a PR into that server branch:

```bash
git fetch origin && git switch -c work/<nn>-<service>-<flavour>-<topic> origin/mcp/<nn>-<service>-<flavour>
# ... commit and push as often as you like
git push -u origin work/<nn>-<service>-<flavour>-<topic>
gh pr create --base mcp/<nn>-<service>-<flavour>   # the base is the server branch, never main
# after the owner merges (merge commit) and CI is green:
git push origin --delete work/<nn>-<service>-<flavour>-<topic>
git switch mcp/<nn>-<service>-<flavour> && git pull && git branch -D work/<nn>-<service>-<flavour>-<topic>
```

One work branch per piece of work, not per commit. If a PR into `main` ever appears, close it
without merging.

Cloud Claude sessions cannot delete remote branches (the git proxy answers `403`), so the owner
deletes the work branch with the PR page's "Delete branch" button, or turns on
Settings → General → "Automatically delete head branches".

## 6. Update the index on `main`

```bash
git switch main
# README.md: add a row to the server index
# docs/roadmap.md: set the server's status and branch
# docs/learnings.md: add what the server teaches
# docs/decisions.md: add any decision you made on the user's behalf
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
