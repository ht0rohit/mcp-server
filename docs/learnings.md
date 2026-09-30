# Learnings

One line per best practice, with the server that demonstrates it. Add to this list as each
server teaches something new.

## SDK

- **Use mcp v2 and pin the major version.** `MCPServer` replaced v1's `FastMCP`, and
  `mcp.server.fastmcp` now raises on import. Pin `mcp>=2.2,<3` and commit `uv.lock`.
