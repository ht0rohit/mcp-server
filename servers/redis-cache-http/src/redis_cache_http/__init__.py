"""A Redis cache MCP server, over Streamable HTTP."""

from .server import main, mcp

__all__ = ["main", "mcp"]
