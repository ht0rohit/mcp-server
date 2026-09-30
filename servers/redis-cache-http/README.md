# redis-cache-http

Server 03 in the series, and the first one built for **Streamable HTTP** from the start. It is a
shared key-value cache backed by Redis: store text or JSON under a namespaced key with an expiry,
read it back, list and delete keys. The service is deliberately simple, so the code can focus on
what changes when an MCP server becomes a long-running HTTP service that many clients share:
one connection pool opened in the lifespan, stateless requests, a health route, bearer-token
auth, and DNS-rebinding protection. Built on the official Python SDK v2 (`MCPServer`).

It does not build on servers 01 or 02 (different service), so there is no tool parity to keep.

## What it exposes

**Tools.** Each returns a Pydantic model, so each publishes an `outputSchema`.

| Tool | Redis | Annotations | Notes |
|---|---|---|---|
| `cache_get` | `GET` + `TTL` (one pipeline) | read-only | A miss is `found: false`, not an error |
| `cache_set` | `SET key value EX ttl GET` | destructive, idempotent | Every value expires: default 1 hour, max `CACHE_MAX_TTL_SECONDS`; says whether it `replaced` a value |
| `cache_delete` | `UNLINK` | destructive, idempotent | `deleted: false` when nothing was there |
| `cache_list_keys` | `SCAN MATCH` | read-only | Glob on the key, pages with `next_cursor`; never `KEYS` |

Every key is stored as `<CACHE_KEY_PREFIX>:<namespace>:<key>` (default prefix `mcpcache`).
Namespaces allow letters, digits, `_` and `-`; keys also allow `.` and `:`. Neither allows glob
characters, so a key can never act as a wildcard.

**Resources**

| URI | What |
|---|---|
| `cache://stats` | JSON: Redis version, memory, hit rate, and how many keys this server owns |
| `cache://{namespace}/{key}` | Template: the stored value. Missing → not found (`-32602`) |

**HTTP routes**

| Route | Auth | What |
|---|---|---|
| `POST /mcp` | Bearer token, when `MCP_AUTH_TOKEN` is set | The MCP endpoint (stateless) |
| `GET /.well-known/oauth-protected-resource/mcp` | None | RFC 9728 metadata, only when auth is on. It names this server as the authorization server as a placeholder: nothing here issues tokens, so clients send the static `Authorization` header |
| `GET /healthz` | None | `200 {"status":"ok"}` when Redis answers a ping, `503` otherwise. Reveals nothing else |

## How the code is organized

```
src/redis_cache_http/
├── server.py   # the MCP part: tools, resources, health route,
│               # create_server(settings), create_app(settings), main()
├── cache.py    # RedisCache: key naming and Redis calls, no MCP; CacheUnavailableError
├── config.py   # Settings from the environment, validated at startup (secure defaults)
├── auth.py     # StaticTokenVerifier (constant-time compare)
├── models.py   # the Pydantic models tools and resources return
└── __main__.py
tests/
├── conftest.py        # fakeredis (or a real Redis via REDIS_TEST_URL), clients in-process and over HTTP
├── test_tools.py      # every tool through the protocol, schemas, annotations, error mapping
├── test_resources.py  # stats and values by URI, not-found vs other errors
├── test_http.py       # the real ASGI app: health, 401s, metadata, Host/Origin checks, a tool call
└── test_config.py     # exposure guard, derived URLs, config errors never echo the token
```

Start with `server.py`: its docstring lists what is new, and the file reads top to bottom.

## Run it

You need a Redis. Locally: `docker run --rm -p 6379:6379 redis:7` (or `redis-server`).

```bash
# from the repo root
uv sync --all-packages
uv run --package redis-cache-http redis-cache-http        # http://127.0.0.1:8000/mcp
```

`.env.example` lists every setting. On `127.0.0.1` no token is needed; to require one:

```bash
export MCP_AUTH_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

The server refuses to start if Redis is unreachable, if `MCP_HOST` is not a loopback address
and `MCP_AUTH_TOKEN` or `MCP_PUBLIC_URL` is missing, or if the token is shorter than 32
characters.

Try it in the MCP Inspector: run `npx @modelcontextprotocol/inspector`, choose "Streamable
HTTP", enter `http://127.0.0.1:8000/mcp`, and add `Authorization: Bearer <token>` if you set one.

### Claude Code

```bash
claude mcp add --transport http redis-cache http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer <token>"
```

### Deploying it

- Put it behind TLS. Set `MCP_HOST=0.0.0.0`, `MCP_AUTH_TOKEN`, and `MCP_PUBLIC_URL` to the base
  URL clients use (`https://cache.example.com`, without `/mcp`). The public URL's host is
  accepted as a `Host` header; add others (a proxy's) to `MCP_ALLOWED_HOSTS`, or every request
  gets `421 Invalid Host header`.
- It is stateless, so it scales out: `uvicorn --factory redis_cache_http.server:create_app
  --workers 4`, or several replicas behind a load balancer, all pointing at the same Redis.
- Point your orchestrator's health check at `/healthz`.

## Test it

```bash
uv run pytest servers/redis-cache-http                                  # fakeredis, no network
REDIS_TEST_URL=redis://localhost:6379/15 uv run pytest servers/redis-cache-http   # real Redis; flushes db 15
```

## What was verified where

Verified in a cloud session (Linux, Redis 7.0.15, SDK `mcp` 2.2.0):

- All 28 tests, on fakeredis **and** on a real Redis.
- The real server started with `uv run redis-cache-http`, `python -m redis_cache_http` and
  `uvicorn --factory`, driven with curl and the SDK's HTTP client: every tool and resource, a
  Redis outage mid-run (tools and resources return "unavailable", `/healthz` goes `503` and
  back to `200` without a restart), 401 without or with a wrong token, 421 on a foreign Host,
  403 on a foreign Origin, the metadata following `MCP_PORT`, and each startup refusal.

Needs your machine: real clients (Claude Desktop, Claude Code, the Inspector UI), and a deployment
behind TLS and a proxy.
