# Roadmap

The series builds MCP servers from basic to advanced. Each step reuses what the previous one
taught and adds a small number of new ideas, so you can diff two servers and see exactly what
changed. The service (X, GitHub, a local database, ...) is just a vehicle; the MCP features are
the point.

| # | Server | Transport | New ideas | Status |
|---|---|---|---|---|
| 01 | `x-basic-stdio` | stdio | Tools, resources, resource templates, prompts, lifespan, tool annotations, `ToolError`, validated arguments, cursor pagination, offline tests | Next |
| 02 | `*-http` | Streamable HTTP | Same server over HTTP, stateless mode, bearer-token auth, health route, running behind a URL | Planned |
| 03 | `*-structured` | either | Structured output (`outputSchema`), progress notifications, logging, completions | Planned |
| 04 | `*-interactive` | either | Elicitation (ask the user), sampling (ask the client's model), roots | Planned |
| 05 | `*-production` | Streamable HTTP | OAuth, middleware, OpenTelemetry, subscriptions, caching hints, deployment | Planned |

Pick the service for each step when you start it, and update this table in the same PR.
