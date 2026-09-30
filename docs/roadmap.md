# Roadmap

The series builds MCP servers from basic to advanced. Each step adds a few new ideas on top of
the previous one, so you can diff two server branches and see exactly what changed. The service
(X, GitHub, a local database, ...) is only the vehicle; the MCP features are the point.

Each server lives on its own branch (see [CLAUDE.md](../CLAUDE.md)). Update this table on `main`
when a server branch is pushed.

| # | Server | Transport | New ideas | Branch |
|---|---|---|---|---|
| 01 | X basic | stdio | Tools, resources, resource templates, prompts, lifespan, tool annotations, `ToolError`, validated arguments, cursor pagination, offline tests | [`mcp/01-x-basic-stdio`](../../../tree/mcp/01-x-basic-stdio) |
| 02 | X advanced | Streamable HTTP | Structured output, progress, completions, elicitation to confirm writes, subscriptions, bearer-token auth with per-tool scopes, health route, Host allowlist, middleware, safe retries, app factory | [`mcp/02-x-advanced`](../../../tree/mcp/02-x-advanced) |
| 03 | HTTP | Streamable HTTP | The same ideas over HTTP: stateless mode, bearer-token auth, health route, running behind a URL | In progress |
| 04 | Production | Streamable HTTP | OAuth, middleware, OpenTelemetry, subscriptions, caching hints, deployment | Planned |

Numbers are assigned when a server branch is created, so rows may be renumbered to match.
