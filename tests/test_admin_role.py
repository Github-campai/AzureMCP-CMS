"""/admin/* needs more than data access.

The admin endpoints expose every user's audit trail -- their UPN and the SQL
their questions produced. Before this, any authorised user could read what
everyone else had been asking, including a customer end-user reading Campai's
queries.
"""

import json
from unittest.mock import patch

import pytest

from admcms_mcp.auth import Forbidden, check_admin_access
from admcms_mcp.server import build_app

CAMPAI_ADMIN_GROUP = "bd6ce8cc-033a-482e-a7cd-acd145e6d93f"
CMS_USER_GROUP = "68f3ad86-6967-4540-92e5-b8b425ca720d"


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    monkeypatch.setenv("AUTH_DISABLED", "false")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clear_allowlist_cache():
    from admcms_mcp import auth

    auth._ALLOWLIST_CACHE["data"] = None
    auth._ALLOWLIST_CACHE["fetched_at"] = 0.0
    yield
    auth._ALLOWLIST_CACHE["data"] = None
    auth._ALLOWLIST_CACHE["fetched_at"] = 0.0


def _allowlist(admins=None):
    # Both groups have data access, as in production: the admin check narrows
    # /admin/* further, it is not a way around the allowlist.
    body = {
        "users": ["gewone-gebruiker-oid"],
        "groups": [CMS_USER_GROUP, CAMPAI_ADMIN_GROUP],
    }
    if admins is not None:
        body["admins"] = admins
    return json.dumps(body)


# ---------------------------------------------------------------------------
# check_admin_access
# ---------------------------------------------------------------------------


def test_admin_group_gets_in():
    with patch(
        "admcms_mcp.auth.get_secret", return_value=_allowlist({"groups": [CAMPAI_ADMIN_GROUP]})
    ):
        check_admin_access({"oid": "x", "upn": "janneke@campai.nl", "groups": [CAMPAI_ADMIN_GROUP]})


def test_admin_listed_by_oid_gets_in():
    with patch("admcms_mcp.auth.get_secret", return_value=_allowlist({"users": ["baas-oid"]})):
        check_admin_access({"oid": "baas-oid", "upn": "baas@campai.nl", "groups": []})


def test_data_access_alone_is_not_enough():
    """The whole point: a customer end-user must not read Campai's audit trail."""
    allowlist = _allowlist({"groups": [CAMPAI_ADMIN_GROUP]})
    with (
        patch("admcms_mcp.auth.get_secret", return_value=allowlist),
        pytest.raises(Forbidden, match="Not an administrator"),
    ):
        check_admin_access(
            {"oid": "cms-user", "upn": "iemand@cmsnl.com", "groups": [CMS_USER_GROUP]}
        )


def test_being_on_the_user_allowlist_is_not_enough():
    allowlist = _allowlist({"groups": [CAMPAI_ADMIN_GROUP]})
    with (
        patch("admcms_mcp.auth.get_secret", return_value=allowlist),
        pytest.raises(Forbidden),
    ):
        check_admin_access({"oid": "gewone-gebruiker-oid", "upn": "u@x.nl", "groups": []})


def test_fails_closed_without_an_admins_section():
    """An unreachable admin UI is recoverable; leaking everyone's audit is not."""
    with (
        patch("admcms_mcp.auth.get_secret", return_value=_allowlist()),
        pytest.raises(Forbidden, match="No administrators are configured"),
    ):
        check_admin_access({"oid": "x", "upn": "y", "groups": [CAMPAI_ADMIN_GROUP]})


def test_fails_closed_on_an_empty_admins_section():
    with (
        patch("admcms_mcp.auth.get_secret", return_value=_allowlist({"users": [], "groups": []})),
        pytest.raises(Forbidden, match="No administrators are configured"),
    ):
        check_admin_access({"oid": "x", "upn": "y", "groups": []})


def test_missing_groups_claim_does_not_crash():
    """Entra omits `groups` entirely when the claim is not configured."""
    allowlist = _allowlist({"groups": [CAMPAI_ADMIN_GROUP]})
    with (
        patch("admcms_mcp.auth.get_secret", return_value=allowlist),
        pytest.raises(Forbidden),
    ):
        check_admin_access({"oid": "x", "upn": "y"})


def test_unreadable_allowlist_denies_rather_than_allows():
    with (
        patch("admcms_mcp.auth.get_secret", side_effect=RuntimeError("Key Vault down")),
        pytest.raises(Forbidden, match="Allowlist unavailable"),
    ):
        check_admin_access({"oid": "x", "upn": "y", "groups": [CAMPAI_ADMIN_GROUP]})


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


async def test_admin_endpoint_rejects_a_non_admin(app_client):
    claims = {"oid": "cms-user", "upn": "iemand@cmsnl.com", "groups": [CMS_USER_GROUP]}
    with (
        patch("admcms_mcp.server.validate_jwt", return_value=claims),
        patch(
            "admcms_mcp.auth.get_secret", return_value=_allowlist({"groups": [CAMPAI_ADMIN_GROUP]})
        ),
    ):
        async with app_client(build_app()) as client:
            response = await client.get("/admin/audit", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 403
    assert "administrator" in response.json()["detail"].lower()


async def test_admin_endpoint_allows_an_admin(app_client):
    claims = {"oid": "janneke", "upn": "janneke@campai.nl", "groups": [CAMPAI_ADMIN_GROUP]}
    with (
        patch("admcms_mcp.server.validate_jwt", return_value=claims),
        patch(
            "admcms_mcp.auth.get_secret", return_value=_allowlist({"groups": [CAMPAI_ADMIN_GROUP]})
        ),
    ):
        async with app_client(build_app()) as client:
            response = await client.get("/admin/audit", headers={"Authorization": "Bearer fake"})
    assert response.status_code != 403


async def test_the_mcp_endpoint_is_unaffected(app_client):
    """A CMS end-user keeps their data access; only /admin/* is narrowed."""
    claims = {"oid": "cms-user", "upn": "iemand@cmsnl.com", "groups": [CMS_USER_GROUP]}
    with (
        patch("admcms_mcp.server.validate_jwt", return_value=claims),
        patch(
            "admcms_mcp.auth.get_secret", return_value=_allowlist({"groups": [CAMPAI_ADMIN_GROUP]})
        ),
    ):
        async with app_client(build_app()) as client:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={
                    "Authorization": "Bearer fake",
                    "Accept": "application/json, text/event-stream",
                },
            )
    assert response.status_code != 403


async def test_admin_group_without_data_access_still_gets_nowhere(app_client):
    """Admin is a narrowing, not a bypass: check_access runs first."""
    claims = {"oid": "vreemde", "upn": "x@elders.nl", "groups": [CAMPAI_ADMIN_GROUP]}
    allowlist = json.dumps({"users": [], "groups": [], "admins": {"groups": [CAMPAI_ADMIN_GROUP]}})
    with (
        patch("admcms_mcp.server.validate_jwt", return_value=claims),
        patch("admcms_mcp.auth.get_secret", return_value=allowlist),
    ):
        async with app_client(build_app()) as client:
            response = await client.get("/admin/audit", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 403
    assert "allowlist" in response.json()["detail"].lower()
