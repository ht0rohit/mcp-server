"""The HTTP layer, in-process: routes, auth, per-tool scopes and the Host allowlist."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def test_health_route_needs_no_token(http):
    response = await http.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_requests_without_a_valid_token_are_refused(http):
    for headers in ({}, {"Authorization": "Bearer not-a-real-token"}):
        response = await http.post("/mcp", json={}, headers=headers)
        assert response.status_code == 401
        assert "resource_metadata=" in response.headers["www-authenticate"]


async def test_protected_resource_metadata(http, config):
    response = await http.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == config.public_url
    assert body["scopes_supported"] == ["x:read"]


async def test_unknown_host_headers_are_refused(http, config):
    # DNS-rebinding protection: a page on evil.example can't drive a server on localhost,
    # even with a valid token.
    token = next(iter(config.auth_tokens))
    headers = {"Authorization": f"Bearer {token}", "Host": "evil.example"}
    response = await http.post("/mcp", json={}, headers=headers)
    assert response.status_code == 421


async def test_read_token_can_read_over_http(app, connect):
    http_client, client = connect("read")
    async with app.router.lifespan_context(app), http_client, client:
        assert client.server_info.name == "x-advanced-http"
        result = await client.call_tool("x_get_user", {"username": "XDevelopers"})
        assert not result.is_error
        assert result.structured_content["username"] == "XDevelopers"


async def test_read_token_cannot_post_and_is_never_asked(app, connect, fake_x, answers):
    http_client, client = connect("read")
    async with app.router.lifespan_context(app), http_client, client:
        result = await client.call_tool("x_create_post", {"text": "Hi"})
    assert result.is_error
    assert "x:write" in "\n".join(block.text for block in result.content)
    assert answers.questions == []  # the scope check runs before the confirmation
    assert fake_x.created == []


async def test_write_token_posts_after_confirmation_over_http(app, connect, fake_x, answers):
    http_client, client = connect("write")
    async with app.router.lifespan_context(app), http_client, client:
        result = await client.call_tool("x_create_post", {"text": "Over HTTP"})
    assert not result.is_error, result.content
    assert result.structured_content["status"] == "posted"
    assert len(answers.questions) == 1
    assert fake_x.created == [{"text": "Over HTTP"}]
