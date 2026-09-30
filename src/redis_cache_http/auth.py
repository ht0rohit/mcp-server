"""Who may call this server.

Over Streamable HTTP an MCP server is an OAuth 2.1 *resource server*: it checks the bearer
token on every request and never issues one. The SDK does the HTTP part (the 401 with a
`WWW-Authenticate` header, the RFC 9728 metadata at /.well-known/oauth-protected-resource);
all we write is a `TokenVerifier`.

This one accepts a single static token from the environment, which suits a personal server.
Real OAuth (server 04) swaps in a verifier that checks JWTs; nothing else changes.
"""

from __future__ import annotations

import hmac

from mcp.server.auth.provider import AccessToken, TokenVerifier


class StaticTokenVerifier(TokenVerifier):
    def __init__(self, token: str, resource: str) -> None:
        self._token = token.encode()
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        # Constant time: `==` stops at the first wrong character, and that timing difference
        # can leak the token one character at a time.
        if not hmac.compare_digest(self._token, token.encode()):
            return None
        return AccessToken(
            token=token,
            client_id="static-token",  # a name for logs; never log the token itself
            scopes=[],
            resource=self._resource,
        )
