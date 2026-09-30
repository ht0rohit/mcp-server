# Adding a server

1. Branch from the latest `main`:

   ```bash
   git switch main && git pull
   git switch -c mcp/<nn>-<service>-<flavour>
   ```

2. Create the package (it becomes a uv workspace member automatically):

   ```bash
   uv init --package --name <service>-<flavour> servers/<service>-<flavour>
   uv add --package <service>-<flavour> "mcp>=2.2,<3"
   ```

   Then shape it like the existing servers: `src/<package>/server.py` with a `main()` that the
   `[project.scripts]` entry points to, a `README.md`, a `.env.example` and a `tests/` folder.

3. Write tests that need no network or secrets:
   - in-process: `async with Client(server) as client: ...`
   - HTTP to the upstream API: `httpx.MockTransport`
   - one smoke test that spawns the real process (stdio) or starts the app (HTTP)

4. Check locally, then push and open a PR into `main`:

   ```bash
   uv sync --all-packages && uv run ruff check . && uv run ruff format --check . && uv run pytest
   ```

5. In the same PR: add the server to the README index, tick it in `docs/roadmap.md`, and add any
   new lessons to `docs/learnings.md`.

## stdio or Streamable HTTP?

| | stdio | Streamable HTTP |
|---|---|---|
| Who starts it | The client launches it as a subprocess | You run it; clients connect to a URL |
| Good for | Local tools, local files, secrets on your machine | Shared or remote servers, web clients |
| Never | `print()` to stdout: stdout is the protocol | Expose it without auth or bind `0.0.0.0` casually |
| Auth | Env vars set in the client config | Bearer token or OAuth on the endpoint |
| Run | `mcp.run()` | `mcp.run(transport="streamable-http")` |

## What can be verified where

Anything about your code and the protocol (unit tests, the stdio smoke test, the Inspector against
a local process) works in a cloud session or on your machine. Real clients (Claude Desktop, Claude
Code), your local files and live upstream API calls with your token need your machine. Each
server README says which of these were checked.
