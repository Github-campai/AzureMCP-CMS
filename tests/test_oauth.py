"""OAuth discovery, DCR stub and Entra proxy.

These cover the contract an MCP client relies on when the user pastes nothing
but a URL: discover, register, authorize, exchange.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import Response

from admcms_mcp.oauth import _rewrite_scope, resource_uri
from admcms_mcp.server import build_app

APP_ID = "1e6983d3-f996-4581-9518-e6819b79ac6a"


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", APP_ID)
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id,cms-tenant-id")
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_MCP_GET", raising=False)
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def test_resource_uri_does_not_double_prefix():
    assert resource_uri(APP_ID) == f"api://{APP_ID}"
    assert resource_uri(f"api://{APP_ID}") == f"api://{APP_ID}"


def test_rewrite_scope_expands_short_form_and_adds_offline_access():
    result = _rewrite_scope("admcms.read").split()
    assert f"api://{APP_ID}/admcms.read" in result
    assert "offline_access" in result


def test_rewrite_scope_keeps_openid_scopes_and_deduplicates():
    result = _rewrite_scope("openid profile admcms.read offline_access").split()
    assert result.count("offline_access") == 1
    assert "openid" in result and "profile" in result


def test_rewrite_scope_accepts_already_qualified_scope():
    result = _rewrite_scope(f"api://{APP_ID}/admcms.read").split()
    assert result.count(f"api://{APP_ID}/admcms.read") == 1


def test_rewrite_scope_defaults_when_client_sends_nothing():
    assert f"api://{APP_ID}/admcms.read" in _rewrite_scope("").split()


# --------------------------------------------------------------------------
# RFC 9728 — protected resource metadata
# --------------------------------------------------------------------------


async def test_protected_resource_metadata_names_the_mcp_endpoint(app_client):
    async with app_client(build_app()) as client:
        response = await client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    body = response.json()
    assert body["resource"].endswith("/mcp")
    assert body["scopes_supported"] == ["admcms.read"]


async def test_protected_resource_metadata_served_on_path_inserted_url(app_client):
    """The path-inserted form is what RFC 9728 clients try first."""
    async with app_client(build_app()) as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    assert response.json()["resource"].endswith("/mcp")


async def test_authorization_server_is_this_host_not_entra(app_client):
    """Entra publishes no registration_endpoint, so we must advertise ourselves."""
    async with app_client(build_app()) as client:
        response = await client.get("/.well-known/oauth-protected-resource")
    servers = response.json()["authorization_servers"]
    assert servers == ["http://test"]
    assert not any("login.microsoftonline.com" in s for s in servers)


async def test_metadata_honours_forwarded_proto(app_client):
    """Behind the Container Apps ingress uvicorn only ever sees plain http."""
    async with app_client(build_app()) as client:
        response = await client.get(
            "/.well-known/oauth-protected-resource",
            headers={"x-forwarded-proto": "https", "x-forwarded-host": "admcms.example.com"},
        )
    body = response.json()
    assert body["resource"] == "https://admcms.example.com/mcp"
    assert body["authorization_servers"] == ["https://admcms.example.com"]


async def test_metadata_prefers_configured_public_base_url(app_client, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://admcms.campai.nl/")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()
    async with app_client(build_app()) as client:
        response = await client.get("/.well-known/oauth-protected-resource")
    assert response.json()["resource"] == "https://admcms.campai.nl/mcp"


# --------------------------------------------------------------------------
# RFC 8414 — authorization server metadata
# --------------------------------------------------------------------------


async def test_authorization_server_metadata_advertises_dcr_and_pkce(app_client):
    async with app_client(build_app()) as client:
        response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    body = response.json()
    assert body["registration_endpoint"] == "http://test/register"
    assert body["authorization_endpoint"] == "http://test/authorize"
    assert body["token_endpoint"] == "http://test/token"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]
    assert "refresh_token" in body["grant_types_supported"]


async def test_authorization_server_metadata_on_path_inserted_url(app_client):
    async with app_client(build_app()) as client:
        response = await client.get("/.well-known/oauth-authorization-server/mcp")
    assert response.status_code == 200
    assert "registration_endpoint" in response.json()


# --------------------------------------------------------------------------
# RFC 7591 — dynamic client registration stub
# --------------------------------------------------------------------------


async def test_register_returns_the_preregistered_entra_app(app_client):
    async with app_client(build_app()) as client:
        response = await client.post(
            "/register",
            json={
                "client_name": "Claude",
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "none",
            },
        )
    assert response.status_code == 201
    body = response.json()
    assert body["client_id"] == APP_ID
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]


async def test_register_tolerates_a_body_that_is_not_json(app_client):
    async with app_client(build_app()) as client:
        response = await client.post(
            "/register", content=b"not json", headers={"content-type": "application/json"}
        )
    assert response.status_code == 201
    assert response.json()["client_id"] == APP_ID


# --------------------------------------------------------------------------
# /authorize proxy
# --------------------------------------------------------------------------


async def test_authorize_redirects_to_entra_with_rewritten_scope(app_client):
    async with app_client(build_app()) as client:
        response = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": APP_ID,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "scope": "admcms.read",
                "state": "xyz",
                "code_challenge": "abc",
                "code_challenge_method": "S256",
                # RFC 8707 — MCP clients send this; the v2.0 endpoint rejects it.
                "resource": "http://test/mcp",
            },
        )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?"
    )
    assert "resource=" not in location
    assert "state=xyz" in location
    assert "code_challenge_method=S256" in location
    assert f"api%3A%2F%2F{APP_ID}%2Fadmcms.read" in location
    assert "offline_access" in location


async def test_authorize_fails_loudly_without_app_id(app_client, monkeypatch):
    monkeypatch.setenv("ENTRA_APP_ID", "")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()
    async with app_client(build_app()) as client:
        response = await client.get("/authorize", params={"response_type": "code"})
    assert response.status_code == 500


# --------------------------------------------------------------------------
# /token proxy
# --------------------------------------------------------------------------


def _mock_httpx(status_code: int, payload: bytes):
    upstream = MagicMock()
    upstream.status_code = status_code
    upstream.content = payload
    upstream.headers = {"content-type": "application/json"}

    instance = MagicMock()
    instance.post = AsyncMock(return_value=upstream)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=instance)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, instance


async def test_token_proxies_to_entra_and_strips_resource(app_client):
    ctx, instance = _mock_httpx(200, b'{"access_token":"redacted","token_type":"Bearer"}')
    with patch("admcms_mcp.oauth.httpx.AsyncClient", return_value=ctx):
        async with app_client(build_app()) as client:
            response = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": "the-code",
                    "client_id": APP_ID,
                    "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                    "code_verifier": "verifier",
                    "scope": "admcms.read",
                    "resource": "http://test/mcp",
                },
            )

    assert response.status_code == 200
    url, kwargs = instance.post.call_args[0][0], instance.post.call_args[1]
    assert url == "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
    sent = kwargs["data"]
    assert "resource" not in sent
    assert sent["code"] == "the-code"
    assert f"api://{APP_ID}/admcms.read" in sent["scope"]


async def test_token_forwards_entra_error_status(app_client):
    ctx, _ = _mock_httpx(400, b'{"error":"invalid_grant"}')
    with patch("admcms_mcp.oauth.httpx.AsyncClient", return_value=ctx):
        async with app_client(build_app()) as client:
            response = await client.post(
                "/token",
                data={"grant_type": "authorization_code", "code": "bad", "client_id": APP_ID},
            )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_token_rewrites_scope_on_refresh(app_client):
    ctx, instance = _mock_httpx(200, b"{}")
    with patch("admcms_mcp.oauth.httpx.AsyncClient", return_value=ctx):
        async with app_client(build_app()) as client:
            await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": "rt",
                    "client_id": APP_ID,
                    "scope": "admcms.read",
                },
            )
    sent = instance.post.call_args[1]["data"]
    assert f"api://{APP_ID}/admcms.read" in sent["scope"]


# --------------------------------------------------------------------------
# Proxy hardening — the proxy only brokers what we advertise
# --------------------------------------------------------------------------


async def test_authorize_refuses_a_foreign_client_id(app_client):
    """Otherwise /authorize is an open relay: our domain, someone else's app."""
    async with app_client(build_app()) as client:
        response = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "11111111-2222-3333-4444-555555555555",
                "redirect_uri": "https://attacker.example/callback",
                "scope": "admcms.read",
            },
        )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "location" not in response.headers


async def test_authorize_requires_a_client_id(app_client):
    async with app_client(build_app()) as client:
        response = await client.get(
            "/authorize", params={"response_type": "code", "scope": "admcms.read"}
        )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


async def test_authorize_accepts_the_application_id_uri_form(app_client):
    async with app_client(build_app()) as client:
        response = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": f"api://{APP_ID}",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "scope": "admcms.read",
            },
        )
    assert response.status_code == 302


@pytest.mark.parametrize("grant_type", ["password", "client_credentials", "implicit", ""])
async def test_token_refuses_grant_types_we_do_not_advertise(app_client, grant_type):
    ctx, instance = _mock_httpx(200, b"{}")
    with patch("admcms_mcp.oauth.httpx.AsyncClient", return_value=ctx):
        async with app_client(build_app()) as client:
            response = await client.post(
                "/token",
                data={
                    "grant_type": grant_type,
                    "client_id": APP_ID,
                    "username": "someone",
                    "password": "hunter2",
                },
            )
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"
    instance.post.assert_not_called()  # nothing may reach Entra


async def test_token_refuses_a_foreign_client_id(app_client):
    ctx, instance = _mock_httpx(200, b"{}")
    with patch("admcms_mcp.oauth.httpx.AsyncClient", return_value=ctx):
        async with app_client(build_app()) as client:
            response = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": "the-code",
                    "client_id": "11111111-2222-3333-4444-555555555555",
                },
            )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    instance.post.assert_not_called()


def test_advertised_grant_types_match_what_the_proxy_accepts():
    """Metadata and enforcement must not drift apart."""
    from admcms_mcp.oauth import ALLOWED_GRANT_TYPES

    assert frozenset({"authorization_code", "refresh_token"}) == ALLOWED_GRANT_TYPES


# --------------------------------------------------------------------------
# 401 challenge
# --------------------------------------------------------------------------


async def test_challenge_points_at_https_metadata_behind_proxy(app_client):
    async with app_client(build_app()) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            headers={"x-forwarded-proto": "https", "x-forwarded-host": "admcms.example.com"},
        )
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert (
        'resource_metadata="https://admcms.example.com'
        '/.well-known/oauth-protected-resource/mcp"' in challenge
    )
    assert "http://" not in challenge


async def test_unauthenticated_get_on_mcp_is_rejected_by_default(app_client):
    async with app_client(build_app()) as client:
        response = await client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert response.status_code == 401


async def test_unauthenticated_get_on_mcp_can_be_re_enabled(monkeypatch):
    """Asserted at middleware level on purpose.

    Letting the request through to the real MCP app would open an SSE stream
    that never closes — which is exactly why this bypass is off by default, and
    would hang the test run.
    """
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_MCP_GET", "true")
    from admcms_mcp.config import get_settings
    from admcms_mcp.server import EntraAuthMiddleware

    get_settings.cache_clear()

    passed_through = Response(status_code=204)

    async def call_next(_request):
        return passed_through

    middleware = EntraAuthMiddleware(app=None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/mcp/",
            "raw_path": b"/mcp/",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
        }
    )
    assert await middleware.dispatch(request, call_next) is passed_through


# ---------------------------------------------------------------------------
# Multiple hostnames. The app answers on admcms.campai.nl and on the customer's
# own mcp-cms-adm.cmsnl.com; each must describe itself.
# ---------------------------------------------------------------------------

CANONIEK = "https://admcms.campai.nl"
KLANT = "https://mcp-cms-adm.cmsnl.com"


@pytest.fixture
def twee_hostnames(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", CANONIEK)
    monkeypatch.setenv("PUBLIC_BASE_URL_ALIASES", KLANT)
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_each_known_hostname_describes_itself(app_client, twee_hostnames):
    """A client that dialled the customer name must not be handed Campai's."""
    async with app_client(build_app()) as client:
        response = await client.get(
            "/.well-known/oauth-protected-resource/mcp",
            headers={"x-forwarded-host": "mcp-cms-adm.cmsnl.com", "x-forwarded-proto": "https"},
        )
    body = response.json()
    assert body["resource"] == f"{KLANT}/mcp"
    assert body["authorization_servers"] == [KLANT]


async def test_the_canonical_hostname_still_describes_itself(app_client, twee_hostnames):
    async with app_client(build_app()) as client:
        response = await client.get(
            "/.well-known/oauth-protected-resource/mcp",
            headers={"x-forwarded-host": "admcms.campai.nl", "x-forwarded-proto": "https"},
        )
    assert response.json()["resource"] == f"{CANONIEK}/mcp"


async def test_an_unknown_host_falls_back_to_the_canonical_url(app_client, twee_hostnames):
    """A spoofed Host header must not make the server advertise someone else's domain."""
    async with app_client(build_app()) as client:
        response = await client.get(
            "/.well-known/oauth-protected-resource/mcp",
            headers={"x-forwarded-host": "aanvaller.example.com", "x-forwarded-proto": "https"},
        )
    body = response.json()
    assert body["resource"] == f"{CANONIEK}/mcp"
    assert "aanvaller" not in response.text


async def test_the_challenge_follows_the_hostname_too(app_client, twee_hostnames):
    async with app_client(build_app()) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"x-forwarded-host": "mcp-cms-adm.cmsnl.com", "x-forwarded-proto": "https"},
        )
    assert response.status_code == 401
    assert f'resource_metadata="{KLANT}/' in response.headers["www-authenticate"]


async def test_authorization_server_metadata_follows_the_hostname(app_client, twee_hostnames):
    async with app_client(build_app()) as client:
        response = await client.get(
            "/.well-known/oauth-authorization-server",
            headers={"x-forwarded-host": "mcp-cms-adm.cmsnl.com", "x-forwarded-proto": "https"},
        )
    body = response.json()
    assert body["issuer"] == KLANT
    assert body["registration_endpoint"] == f"{KLANT}/register"


async def test_host_matching_ignores_case(app_client, twee_hostnames):
    async with app_client(build_app()) as client:
        response = await client.get(
            "/.well-known/oauth-protected-resource/mcp",
            headers={"x-forwarded-host": "MCP-CMS-ADM.CMSNL.COM", "x-forwarded-proto": "https"},
        )
    assert response.json()["resource"] == f"{KLANT}/mcp"
