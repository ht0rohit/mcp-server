# Decisions

Decisions made on the owner's behalf, newest first, one line each with the reason. Revisit any
of them by changing the code and striking the line through.

## 2026-09-30: server 03 (redis-cache-http)

- **Redis cache over Streamable HTTP as the first HTTP-first server.** Chosen with the owner; a cache keeps the service small and makes the HTTP lessons unavoidable.
- **Package and script keep uv's default names** (`redis_cache_http`, `redis-cache-http`), like server 01.
- **A cache miss is `found: false`, not a `ToolError`.** Misses are normal answers; errors are for real failures.
- **Redis failures raise `CacheUnavailableError(ResourceError)`**: the SDK treats `ResourceError` as anticipated in tools and resources, so one type serves both.
- **Static token auth is optional on loopback and required elsewhere**; the server refuses to start exposed without it or without `MCP_PUBLIC_URL`.
- **`create_server(settings)` factory instead of a module-level `mcp`**: auth is a constructor argument, and tests build their own servers.
- **Tests use fakeredis with a patched `INFO`**; `REDIS_TEST_URL` runs the same suite on a real Redis.

## 2026-09-30: work branches

- **Server branches change only through PRs from `work/...` branches**, merged with a merge commit, and the work branch is deleted after the merge. Asked by the owner after PR #3 put the Redis server on `main` (reverted in `b3b17b1`).
- **Work branches use the `work/` prefix**: git refuses `mcp/<server>/<topic>` while `mcp/<server>` exists.

## 2026-09-30: review of servers 01 and 02

- **CI runs on `mcp/**` pushes.** Server branches are never PR'd, so without it their tests never ran in CI.
- **Server 02 gets `x_get_user_mentions` back.** It was dropped without a reason, and 02 promises 01's read tools.
- **Long posts use `note_tweet`** in both servers. X cuts `text` at 280 characters for long posts.
- **`x_create_post` no longer caps text at 280 code points.** X counts weighted characters and Premium allows more; X's own 400 message is passed to the model instead.
- **A delete that reports `deleted: false` is checked with a lookup.** If the post is gone (a retried delete after a lost response), the tool reports success.
- **`--port` and `--host` go through `Config.from_env(...)`.** The default public URL is then derived from the final port.
- **`issuer_url` stays pointed at the server, documented as a placeholder.** The SDK needs an authorization server URL in the metadata; clients use a static `Authorization` header until the OAuth server (roadmap) exists.
- **Username completions stay shared across callers**, documented. Fine for a personal server; key by `client_id` if it is ever shared.
- **Resources raise `ResourceNotFoundError` only for missing users**, `ResourceError` otherwise.
- **Rate-limit errors say "resets in N seconds"**, not a raw Unix time.
- **Minor cleanups:** user cache eviction, cached "me" ID, one author-map helper, keep at least one item when a result is over the size limit.
