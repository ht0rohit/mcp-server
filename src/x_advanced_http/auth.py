"""Who may call this server, and what they may do.

Over Streamable HTTP the MCP server is an OAuth 2.1 *resource server*: it checks the bearer token
on every request and never issues one. The SDK does the HTTP part (401s, the RFC 9728 metadata
document, `get_access_token()` in handlers); all we write is a `TokenVerifier`.

This verifier checks tokens against a static table from the environment, which is enough for a
personal server. A shared deployment verifies JWTs or calls the identity provider's
introspection endpoint instead (roadmap server 05); nothing else in the server changes.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.mcpserver.exceptions import ToolError


class StaticTokenVerifier(TokenVerifier):
    def __init__(self, tokens: Mapping[str, frozenset[str]], resource: str) -> None:
        self._tokens = dict(tokens)
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        # Compare in constant time, so response timing can't reveal how much of a token matched.
        for known, scopes in self._tokens.items():
            if hmac.compare_digest(known.encode(), token.encode()):
                return AccessToken(
                    token=token,
                    # A stable, non-secret name for logs: never log the token itself.
                    client_id=f"static-{hashlib.sha256(known.encode()).hexdigest()[:8]}",
                    scopes=sorted(scopes),
                    resource=self._resource,
                )
        return None


def require_scope(scope: str) -> None:
    """Refuse the call unless the caller's token grants `scope`.

    The SDK already enforces the scopes every request needs (`required_scopes`). Per-tool
    scopes, like `x:write` for posting, are checked here, inside the tool.

    `get_access_token()` is None only when there is no HTTP layer at all (the in-memory test
    client), where the process boundary is the security boundary, so the call is allowed.
    """
    token = get_access_token()
    if token is not None and scope not in token.scopes:
        raise ToolError(
            f"This token lacks the '{scope}' scope. Ask the server owner for a token that has it."
        )
