"""The HTTP layer, in-process against the real ASGI app: health, auth, metadata, Host checks."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def test_health_needs_no_token(http):
    response = await http.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_requests_without_a_valid_token_are_refused(http):
    for headers in ({}, {"Authorization": "Bearer not-the-token"}):
        response = await http.post("/mcp", json={}, headers=headers)
        assert response.status_code == 401
        assert "oauth-protected-resource/mcp" in response.headers["www-authenticate"]


async def test_protected_resource_metadata_names_the_mcp_endpoint(http):
    response = await http.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    assert response.json()["resource"] == "http://127.0.0.1:8000/mcp"


async def test_unknown_host_is_refused_even_with_the_token(http, token):
    headers = {"Authorization": f"Bearer {token}", "Host": "evil.example"}
    response = await http.post("/mcp", json={}, headers=headers)
    assert response.status_code == 421


async def test_unknown_origin_is_refused(http, token):
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://evil.example"}
    response = await http.post("/mcp", json={}, headers=headers)
    assert response.status_code == 403


async def test_tool_call_over_http_with_the_token(http_client):
    assert http_client.server_info.name == "redis-cache-http"
    await http_client.call_tool("cache_set", {"namespace": "n", "key": "k", "value": "v"})
    result = await http_client.call_tool("cache_get", {"namespace": "n", "key": "k"})
    assert not result.is_error
    assert result.structured_content["value"] == "v"
