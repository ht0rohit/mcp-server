# mcp-server

Learning MCP (Model Context Protocol) server best practices in Python by building a series of
servers, from a basic stdio server to advanced Streamable HTTP ones.

Built with the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (v2)
and [uv](https://docs.astral.sh/uv/).

## How the repo is organized

`main` holds only the shared files: this README, [CLAUDE.md](CLAUDE.md), the docs, CI and the uv
workspace config. **Each server lives on its own branch** cut from `main`, and server branches
are never merged back. To see or run a server, check out its branch.

## Servers

| # | Branch | Transport | What it shows |
|---|---|---|---|
| 01 | [`mcp/01-x-basic-stdio`](../../tree/mcp/01-x-basic-stdio) | stdio | Read-only X (Twitter) server: tools, resources, resource templates, prompts, lifespan |
| 02 | [`mcp/02-x-advanced`](../../tree/mcp/02-x-advanced) | Streamable HTTP | X server end to end: bearer-token auth with scopes, structured output, progress, confirmed writes (elicitation), subscriptions, completions, middleware |

The full plan is in [docs/roadmap.md](docs/roadmap.md).

## Quick start

```bash
git switch mcp/01-x-basic-stdio
uv sync --all-packages     # install the server and the dev tools
uv run pytest              # run its tests (no network or secrets needed)
```

The server's own README explains how to run it and connect it to Claude Desktop, Claude Code or
the MCP Inspector.

## Docs

- [CLAUDE.md](CLAUDE.md): branch rule, conventions and commands
- [Roadmap](docs/roadmap.md)
- [Adding a server](docs/adding-a-server.md)
- [Learnings](docs/learnings.md)
