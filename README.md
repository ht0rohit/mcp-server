# mcp-server

Learning MCP (Model Context Protocol) server best practices in Python by building a series of
servers, from a basic stdio server to advanced Streamable HTTP ones.

Built with the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (v2)
and [uv](https://docs.astral.sh/uv/).

## Servers

`main` holds only the shared files. Each server lives on its own branch, so check out the
branch to see and run it.

| # | Branch | Transport | What it shows |
|---|---|---|---|
| 01 | [`mcp/01-x-basic-stdio`](../../tree/mcp/01-x-basic-stdio) | stdio | Read-only X (Twitter) server: tools, resources, resource templates, prompts, lifespan |

The full plan is in [docs/roadmap.md](docs/roadmap.md).

## Quick start

```bash
uv sync --all-packages     # install every server and the dev tools
uv run pytest              # run all tests (no network or secrets needed)
```

On `main` there are no servers, so check out a server branch first:

```bash
git switch mcp/01-x-basic-stdio
uv sync --all-packages && uv run pytest
```

Each server's README explains how to run it and connect it to a client such as Claude Desktop,
Claude Code or the MCP Inspector.

## Repo layout

```
docs/        roadmap, how to add a server, learnings
servers/     one uv workspace package per server
```

Every new server starts on a branch cut from `main` (`mcp/<nn>-<service>-<flavour>`) and stays
there; it is never merged into `main`. See [docs/adding-a-server.md](docs/adding-a-server.md) and
[CLAUDE.md](CLAUDE.md) for the conventions.

## Docs

- [Roadmap](docs/roadmap.md)
- [Adding a server](docs/adding-a-server.md)
- [Learnings](docs/learnings.md)
