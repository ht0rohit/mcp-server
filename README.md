# mcp-server

Learning MCP (Model Context Protocol) server best practices in Python by building a series of
servers, from a basic stdio server to advanced Streamable HTTP ones.

Built with the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (v2)
and [uv](https://docs.astral.sh/uv/).

## Servers

| # | Server | Transport | What it shows |
|---|---|---|---|
| | _None yet. The first, `x-basic-stdio`, is in progress._ | | |

The full plan is in [docs/roadmap.md](docs/roadmap.md).

## Quick start

```bash
uv sync --all-packages     # install every server and the dev tools
uv run pytest              # run all tests (no network or secrets needed)
```

Each server's README explains how to run it and connect it to a client such as Claude Desktop,
Claude Code or the MCP Inspector.

## Repo layout

```
docs/        roadmap, how to add a server, learnings
servers/     one uv workspace package per server
```

Every new server starts on a branch cut from `main` (`mcp/<nn>-<service>-<flavour>`) and lands
through a pull request. See [docs/adding-a-server.md](docs/adding-a-server.md) and
[CLAUDE.md](CLAUDE.md) for the conventions.

## Docs

- [Roadmap](docs/roadmap.md)
- [Adding a server](docs/adding-a-server.md)
- [Learnings](docs/learnings.md)
