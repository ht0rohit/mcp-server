# Learnings

One line per best practice, with the server that demonstrates it. Add to this list as each
server teaches something new.

## SDK

- **Use mcp v2 and pin the major version.** `MCPServer` replaced v1's `FastMCP`, and
  `mcp.server.fastmcp` now raises on import. Pin `mcp>=2.2,<3` and commit `uv.lock`.

## Server design (branch `mcp/01-x-basic-stdio`)

- **Write `instructions`.** They reach the model once per session: say what the server is for
  and how its tools fit together (for example "pass the cursor back for the next page").
- **Open shared clients in the lifespan.** One `httpx.AsyncClient` for every call, read with
  `ctx.request_context.lifespan_context`, closed on shutdown.
- **Prefix and verb-first tool names** (`x_get_user`) so they don't collide with other servers.
- **Flat, annotated arguments.** `Annotated[int, Field(ge=5, le=100)]` puts the limits in the
  input schema and rejects bad input before your code runs.
- **Annotate tools.** `ToolAnnotations(read_only_hint=True, idempotent_hint=True,
  open_world_hint=True)` lets clients skip confirmation for safe calls.
- **Errors are errors.** Raise `ToolError` in tools and `ResourceNotFoundError` in resources.
  Any other exception is a crash: the model only sees "Error executing tool <name>".
- **Say what to do next in error messages** ("rate limit reached, resets at ...", "your plan may
  not include this endpoint"), because the model reads them.
- **Know the upstream API's quirks.** X returns HTTP 200 with an `errors` array for missing
  items, and each endpoint has its own `max_results` range. Encode both, and test both.
- **Batch tools beat loops.** `x_get_users` takes 100 handles, so the model makes one call, not 100.
- **Paginate with a cursor and cap the size**, dropping whole items rather than cutting text.
- **Plain-text tools set `structured_output=False`.** mcp v2 otherwise wraps a `str` return in
  an `outputSchema` of `{"result": ...}`. Typed output is its own step (server 02).
- **Resources vs tools vs prompts.** Tools are called by the model, resources are read by the
  client for context, prompts are templates the user picks.

## Server design (branch `mcp/02-x-advanced`)

- **Return models, not strings.** A Pydantic return type becomes the tool's `outputSchema`; the
  client gets `structured_content` for the app and the same object as JSON text for the model.
- **Build the server in a factory.** `create_server(config)` takes a validated `Config`, so tests
  and production differ only in config, and bad settings fail at startup with the variable name.
- **Only expose what works.** Register write tools and "me" resources only when the credentials
  they need are configured, rather than listing tools that always fail.
- **Confirm side effects with `Resolve` + `Elicit`.** The confirmation is a parameter the model
  can't see or fill, and the same code serves 2026-07-28 (multi-round-trip) and older clients.
  Build the question only from the arguments: the answer is matched to its exact text.
- **Check permissions before asking the user.** Make the confirmation resolver depend on the
  scope-check resolver, so a caller who can't write is refused without a pointless prompt.
- **Annotate writes honestly**: `destructive_hint=True` for deletes, `idempotent_hint=False` for
  posts.
- **Report progress from long tools.** `ctx.report_progress(done, total, message)` per page;
  values must increase, and it is a no-op when the client didn't ask.
- **Tell subscribers what changed.** `ctx.notify_resource_updated(uri)` after a write reaches
  every `subscriptions/listen` stream for that URI; clients refetch.
- **Complete arguments** with one `@mcp.completion()` handler for prompt arguments and
  resource-template parameters. It gets no `Context`, so close over shared state.
- **Retry only what is safe.** Bounded retries with backoff for GET and DELETE on timeouts and
  502/503/504; never retry a POST that could publish twice.
- **Log in middleware, never tokens.** An `async (ctx, call_next)` middleware sees every message;
  log the method, caller id and duration. The MCP logging capability is deprecated in 2026-07-28.

## Streamable HTTP (branch `mcp/02-x-advanced`)

- **Your server is an OAuth resource server.** Implement `TokenVerifier`, pass `AuthSettings`
  with `required_scopes`, and the SDK adds the 401s and the RFC 9728 metadata route.
- **Check per-tool scopes yourself** with `get_access_token()`; it is `None` only without HTTP.
- **Refuse to start without auth** on an HTTP server, and compare tokens in constant time.
- **Allowlist Host headers** with `TransportSecuritySettings`: the default only accepts
  localhost, and a deployed server answers `421` until its hostname is listed.
- **Add a health route** with `custom_route`; it skips auth, so keep it to public facts.
- **Share `RequestStateSecurity` keys across workers**, or a confirmation answered on one worker
  can't be finished on another.
- **The lifespan runs once per process** over HTTP, so pools and caches there are shared by every
  request.

## stdio

- **stdout is the protocol.** Log to stderr, and test that nothing else reaches stdout.
- **Fail fast at startup** when a required secret is missing, with a message on stderr.

## Testing

- **Test through the protocol in-process** with `async with Client(server) as client:`.
- **Mock the upstream API at the transport** with `httpx.MockTransport`, so no token or network.
- **Use the anyio pytest plugin** (`pytest.mark.anyio`), not pytest-asyncio: the client's cancel
  scopes must be entered and exited in the same task, which async fixtures under pytest-asyncio
  break.
- **Add one real stdio smoke test** that spawns the server with `StdioServerParameters`.
- **Test HTTP in-process** with `httpx2.ASGITransport` against the real app (routes, 401s, Host
  checks), run the app's lifespan with `app.router.lifespan_context(app)`, and add one test that
  starts the real process on a port.
- **Test both protocol eras.** `Client(server, mode="legacy")` exercises 2025-11-25 sessions;
  the default negotiates 2026-07-28.
