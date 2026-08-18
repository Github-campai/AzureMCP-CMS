"""The connector icon: served publicly and announced in serverInfo."""

import base64

import pytest

from admcms_mcp import branding
from admcms_mcp.server import build_app


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "tenant-a")
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_icon_ships_inside_the_package():
    """A repo-root path would vanish once pip installs only the package."""
    assert branding._ICON_PATH.is_file()
    assert branding._ICON_PATH.parent.name == "assets"
    assert branding._ICON_PATH.parent.parent.name == "admcms_mcp"


def test_icon_is_a_valid_png():
    data = branding.icon_bytes()
    assert data is not None
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_dimensions_are_read_from_the_file_not_hardcoded():
    assert branding.icon_dimensions() == "180x180"


def test_data_uri_round_trips_to_the_same_bytes():
    uri = branding.icon_data_uri()
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == branding.icon_bytes()


def test_https_entry_is_published_only_with_a_public_base_url():
    without = branding.server_icons("")
    assert all(i["src"].startswith("data:") for i in without), (
        "a relative or http icon URL is useless to a client"
    )

    with_base = branding.server_icons("https://admcms.example.com/")
    assert with_base[0]["src"] == "https://admcms.example.com/icon.png"
    assert with_base[0]["sizes"] == ["180x180"]
    assert any(i["src"].startswith("data:") for i in with_base), "data URI must remain as fallback"


def test_http_base_url_is_refused():
    """Behind the ingress the scheme looks like http; never advertise that."""
    icons = branding.server_icons("http://admcms.example.com")
    assert all(i["src"].startswith("data:") for i in icons)


def test_missing_asset_does_not_break_anything(monkeypatch):
    monkeypatch.setattr(branding, "icon_bytes", lambda: None)
    assert branding.server_icons("https://x.example.com") == []


async def test_icon_route_is_public_and_returns_the_png(app_client):
    async with app_client(build_app()) as client:
        response = await client.get("/icon.png")
    assert response.status_code == 200, "must not require a bearer token"
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == branding.icon_bytes()
    assert "max-age" in response.headers.get("cache-control", "")


async def test_server_announces_the_icon_at_initialize(app_client, monkeypatch):
    """Asserted on the wire, not on an internal attribute.

    What matters is the serverInfo a client actually receives.
    """
    import json

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://admcms.example.com")
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()

    async with app_client(build_app()) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert response.status_code == 200
    payload = json.loads(response.text.split("data: ", 1)[1])
    server_info = payload["result"]["serverInfo"]

    assert server_info["websiteUrl"] == "https://www.cmsnl.com"
    icons = server_info["icons"]
    assert icons[0]["src"] == "https://admcms.example.com/icon.png"
    assert icons[0]["mimeType"] == "image/png"
    assert icons[0]["sizes"] == ["180x180"]
    assert any(i["src"].startswith("data:image/png;base64,") for i in icons)
