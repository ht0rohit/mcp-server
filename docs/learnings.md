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
  an `outputSchema` of `{"result": ...}`. Typed output is its own step (server 03).
- **Resources vs tools vs prompts.** Tools are called by the model, resources are read by the
  client for context, prompts are templates the user picks.

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
