from unittest.mock import patch

import pytest

from admcms_mcp.auth import InvalidToken
from admcms_mcp.server import build_app


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    monkeypatch.setenv("AUTH_DISABLED", "false")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()


async def test_mcp_without_token_returns_401_with_www_authenticate(app_client):
    app = build_app()
    async with app_client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )
    assert response.status_code == 401
    assert "Bearer" in response.headers.get("www-authenticate", "")
    assert "resource_metadata" in response.headers.get("www-authenticate", "")


async def test_oauth_protected_resource_metadata_published(app_client):
    app = build_app()
    async with app_client(app) as client:
        response = await client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    body = response.json()
    assert "resource" in body
    assert "authorization_servers" in body
    assert "scopes_supported" in body
    assert "admcms.read" in body["scopes_supported"]


async def test_healthz_is_public(app_client):
    app = build_app()
    async with app_client(app) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200


async def test_mcp_with_valid_token_and_allowlist_passes(app_client):
    claims = {"oid": "user", "upn": "u@campai.nl", "groups": [], "tid": "campai-tenant-id"}
    with (
        patch("admcms_mcp.server.validate_jwt", return_value=claims),
        patch("admcms_mcp.server.check_access", return_value=None),
    ):
        app = build_app()
        async with app_client(app) as client:
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


async def test_mcp_with_valid_token_but_not_on_allowlist_returns_403(app_client):
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
        async with app_client(app) as client:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={"Authorization": "Bearer fake-token"},
            )
    assert response.status_code == 403


async def test_auth_disabled_bypasses_check(app_client, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()

    app = build_app()
    async with app_client(app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code != 401


async def test_index_escapes_reflected_query_parameters(app_client):
    """The consent-callback page reflects Entra's query string back at the user."""
    async with app_client(build_app()) as client:
        response = await client.get(
            "/", params={"admin_consent": "True", "tenant": "<script>alert(1)</script>"}
        )
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


async def test_index_escapes_error_parameters(app_client):
    async with app_client(build_app()) as client:
        response = await client.get(
            "/",
            params={
                "error": "<img src=x onerror=alert(1)>",
                "error_description": "<b>nope</b>",
            },
        )
    assert response.status_code == 400
    assert "<img src=x" not in response.text
    assert "<b>nope</b>" not in response.text


async def test_rejected_token_says_in_the_log_why(app_client, caplog):
    """Een geweigerd token liet eerst geen enkel spoor na.

    Aanleiding: een gastgebruiker kreeg netjes een token van Entra maar werd bij
    elke call geweigerd, en in de logs was daar niets van terug te vinden - de
    reden moest worden afgeleid uit de omliggende requests. De boodschap van
    `validate_jwt` noemt de tenant of de audience die niet klopte; juist dat
    onderscheidt een gast op de verkeerde tenant van een verlopen token.
    """
    with (
        patch(
            "admcms_mcp.server.validate_jwt",
            side_effect=InvalidToken("Untrusted tenant: 9f1c-vreemde-tenant"),
        ),
        caplog.at_level("WARNING", logger="admcms_mcp.server"),
    ):
        app = build_app()
        async with app_client(app) as client:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={"Authorization": "Bearer een.of.ander.token"},
            )

    assert response.status_code == 401
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Untrusted tenant" in logged
    assert "9f1c-vreemde-tenant" in logged
    # Het token zelf hoort er niet in.
    assert "een.of.ander.token" not in logged


async def test_the_usual_tokenless_probe_stays_out_of_the_log(app_client, caplog):
    """Elke MCP-client begint zonder token om de WWW-Authenticate op te halen.

    Dat zijn er tientallen per uur en het is geen fout. Zou dat pad ook loggen,
    dan verdrinkt de weigering die er wel toe doet.
    """
    with caplog.at_level("WARNING", logger="admcms_mcp.server"):
        app = build_app()
        async with app_client(app) as client:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            )

    assert response.status_code == 401
    assert not [r for r in caplog.records if "Rejected token" in r.getMessage()]
