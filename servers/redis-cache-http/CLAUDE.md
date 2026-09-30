# CLAUDE.md (redis-cache-http)

Guidance for this server only. The root `CLAUDE.md` covers the repo-wide rules.

## Files

- `server.py` is the MCP layer only: validate input, call `RedisCache`, shape output. Tools are
  plain functions registered in `create_server(settings)` via the `TOOLS` list.
- `cache.py` holds every Redis call. Wrap new calls in `async with redis_errors():`, which turns
  any `RedisError` into `CacheUnavailableError`, a `ResourceError`. The SDK treats that as an
  anticipated failure in tools **and** resources, so one exception serves both, and the Redis
  address never reaches the client.
- `config.py`: every setting, listed in `.env.example`. Derived values (`public_url`,
  `allowed_hosts`, `allowed_origins`) are properties, so they follow `MCP_PORT`.

## Rules that are easy to break

- Never use `KEYS`, `FLUSHDB` or anything that walks or clears the whole keyspace: the Redis may
  be shared. Use `SCAN` with a bounded number of rounds (`MAX_SCAN_ROUNDS`).
- Every key goes through `RedisCache._full_key`; every write has a TTL.
- Namespaces never contain `:`, and keys never contain glob characters (`*?[]`). The resource
  template validates URI parts itself, because URI parts skip the tool argument schemas.
- A cache miss is data (`found: false` in tools), not an error. In the resource it is
  `ResourceNotFoundError`, because a URI that points at nothing is a wrong address.
- `/healthz` and static resources get no `ctx`; they read the running cache from the `running`
  dict that the lifespan fills in `create_server`. Everything else uses `ctx`.
- The lifespan runs once per app start (the SDK's session manager enters it), not per request.
- Never print or log the auth token. Config errors are formatted in `main()` without input
  values, because pydantic's own message includes them.

## Known limits (deliberate)

- **fakeredis has no `INFO`.** `tests/conftest.py` patches in the fields `cache://stats` reads.
  Everything else behaves like Redis 7: the whole suite also passes with `REDIS_TEST_URL`.
- **Value size is counted in characters** (`MAX_VALUE_CHARS` = 100,000), not bytes; a value of
  multi-byte characters can take up to 4x that in Redis.
- **`owned_keys` in stats is a lower bound** when the bounded SCAN stops early
  (`owned_keys_complete: false`).
- **No client-side retries.** redis-py's async client is configured with 0 retries, so a
  `SET ... GET` is never replayed and `replaced`/`deleted` flags stay truthful.
- **One static token**, no scopes. Real OAuth, per-tool scopes and a real authorization server
  come in server 04; only the `TokenVerifier` changes.
- Keyspace hits and misses in stats are server-wide Redis numbers, not per prefix.
