from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from admcms_mcp.server import build_app


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    monkeypatch.setenv("AUTH_DISABLED", "false")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()


async def test_mcp_without_token_returns_401_with_www_authenticate():
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )
    assert response.status_code == 401
    assert "Bearer" in response.headers.get("www-authenticate", "")
    assert "resource_metadata" in response.headers.get("www-authenticate", "")


async def test_oauth_protected_resource_metadata_published():
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    body = response.json()
    assert "resource" in body
    assert "authorization_servers" in body
    assert "scopes_supported" in body
    assert "admcms.read" in body["scopes_supported"]


async def test_healthz_is_public():
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200


async def test_mcp_with_valid_token_and_allowlist_passes():
    claims = {"oid": "user", "upn": "u@campai.nl", "groups": [], "tid": "campai-tenant-id"}
    with (
        patch("admcms_mcp.server.validate_jwt", return_value=claims),
        patch("admcms_mcp.server.check_access", return_value=None),
    ):
        app = build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={
                    "Authorization": "Bearer fake-token",
                    "Accept": "application/json, text/event-stream",
                },
            )
    # Should NOT be 401 (auth passed). 200/400/422 all acceptable depending on MCP behavior.
    assert response.status_code != 401


async def test_mcp_with_valid_token_but_not_on_allowlist_returns_403():
    from admcms_mcp.auth import Forbidden

    claims = {
        "oid": "stranger",
        "upn": "x@example.com",
        "groups": [],
        "tid": "campai-tenant-id",
    }
    with (
        patch("admcms_mcp.server.validate_jwt", return_value=claims),
        patch("admcms_mcp.server.check_access", side_effect=Forbidden("not allowed")),
    ):
        app = build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={"Authorization": "Bearer fake-token"},
            )
    assert response.status_code == 403


async def test_auth_disabled_bypasses_check(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()

    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code != 401
