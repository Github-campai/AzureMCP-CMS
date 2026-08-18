"""The connector icon: served publicly, announced in serverInfo, format sniffed."""

import base64

import pytest

from admcms_mcp import branding
from admcms_mcp.server import build_app

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Read the shipped asset's real size, so swapping the logo does not break tests
# that assert on dimensions.
_ICON = branding.icon_bytes() or b""
_IS_PNG = _ICON[:8] == PNG_MAGIC
ICON_SIZE = (
    f"{int.from_bytes(_ICON[16:20], 'big')}x{int.from_bytes(_ICON[20:24], 'big')}"
    if _IS_PNG
    else None
)


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


@pytest.fixture(autouse=True)
def clear_branding_caches():
    yield
    branding.icon_bytes.cache_clear()
    branding.icon_media_type.cache_clear()
    branding.icon_dimensions.cache_clear()


def _fake_icon(monkeypatch, data: bytes) -> None:
    monkeypatch.setattr(branding, "icon_bytes", lambda: data)
    branding.icon_media_type.cache_clear()
    branding.icon_dimensions.cache_clear()


def _webp_vp8x(width: int, height: int) -> bytes:
    """Minimal VP8X WebP header: canvas size as two 24-bit values, minus one."""
    return (
        b"RIFF"
        + (0).to_bytes(4, "little")
        + b"WEBP"
        + b"VP8X"
        + (10).to_bytes(4, "little")
        + bytes(4)
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )


def _jpeg() -> bytes:
    return bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(40)


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def test_icon_ships_inside_the_package():
    """A repo-root path resolves in development and 404s in the container."""
    assert branding._ASSET_DIR.is_dir()
    assert branding._ASSET_DIR.name == "assets"
    assert branding._ASSET_DIR.parent.name == "admcms_mcp"
    assert any((branding._ASSET_DIR / name).is_file() for name in branding._ICON_FILENAMES)


def test_the_shipped_asset_is_a_real_image():
    data = branding.icon_bytes()
    assert data
    assert branding.icon_media_type() in ("image/png", "image/webp", "image/jpeg", "image/svg+xml")


# ---------------------------------------------------------------------------
# Format detection. A logo saved as WebP but named icon.png used to be served as
# image/png, which silently fails to render.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _IS_PNG, reason="shipped asset is not a PNG")
def test_png_size_is_read_from_the_header_not_hardcoded():
    assert branding.icon_media_type() == "image/png"
    assert branding.icon_dimensions() == ICON_SIZE


def test_webp_named_png_is_served_as_webp(monkeypatch):
    _fake_icon(monkeypatch, _webp_vp8x(256, 128))
    assert branding.icon_media_type() == "image/webp"
    assert branding.icon_dimensions() == "256x128"


def test_jpeg_is_detected_without_a_size(monkeypatch):
    _fake_icon(monkeypatch, _jpeg())
    assert branding.icon_media_type() == "image/jpeg"
    assert branding.icon_dimensions() is None


def test_svg_is_detected(monkeypatch):
    _fake_icon(monkeypatch, b"  <svg xmlns='http://www.w3.org/2000/svg'></svg>")
    assert branding.icon_media_type() == "image/svg+xml"
    assert branding.icon_dimensions() is None


def test_unknown_format_falls_back_to_png_without_a_size_hint(monkeypatch):
    _fake_icon(monkeypatch, b"not an image at all")
    assert branding.icon_media_type() == "image/png"
    assert branding.icon_dimensions() is None


# ---------------------------------------------------------------------------
# serverInfo.icons
# ---------------------------------------------------------------------------


def test_data_uri_round_trips_to_the_same_bytes():
    uri = branding.icon_data_uri()
    assert uri.startswith(f"data:{branding.icon_media_type()};base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == branding.icon_bytes()


def test_https_entry_is_published_only_with_a_public_base_url():
    without = branding.server_icons("")
    assert all(icon["src"].startswith("data:") for icon in without), (
        "a relative or http icon URL is useless to a client"
    )

    with_base = branding.server_icons("https://admcms.example.com/")
    assert with_base[0]["src"] == "https://admcms.example.com/icon.png"
    assert any(icon["src"].startswith("data:") for icon in with_base), "data URI must remain"


def test_http_base_url_is_refused():
    """Behind the ingress the scheme looks like http; never advertise that."""
    icons = branding.server_icons("http://admcms.example.com")
    assert all(icon["src"].startswith("data:") for icon in icons)


def test_sizes_is_omitted_when_the_format_hides_it(monkeypatch):
    """A wrong sizes hint is worse than none."""
    _fake_icon(monkeypatch, _jpeg())
    icons = branding.server_icons("https://admcms.example.com")
    assert icons[0]["mimeType"] == "image/jpeg"
    assert icons[0]["sizes"] is None


def test_missing_asset_does_not_break_anything(monkeypatch):
    _fake_icon(monkeypatch, b"")
    monkeypatch.setattr(branding, "icon_bytes", lambda: None)
    assert branding.server_icons("https://x.example.com") == []


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


async def test_icon_route_is_public_and_returns_the_asset(app_client):
    async with app_client(build_app()) as client:
        response = await client.get("/icon.png")
    assert response.status_code == 200, "must not require a bearer token"
    assert response.content == branding.icon_bytes()
    assert "max-age" in response.headers.get("cache-control", "")


async def test_route_serves_the_detected_content_type(app_client, monkeypatch):
    _fake_icon(monkeypatch, _webp_vp8x(64, 64))
    async with app_client(build_app()) as client:
        response = await client.get("/icon.png")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/webp")


async def test_server_announces_the_icon_at_initialize(app_client, monkeypatch):
    """Asserted on the wire: what a client actually receives in serverInfo."""
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
    assert icons[0]["mimeType"] == branding.icon_media_type()
    assert any(icon["src"].startswith("data:") for icon in icons)


@pytest.mark.parametrize("path", branding.ICON_ALIAS_ROUTES)
async def test_browser_convention_paths_serve_the_same_icon(app_client, path):
    """Clients that ignore serverInfo.icons probe these instead."""
    async with app_client(build_app()) as client:
        response = await client.get(path)
    assert response.status_code == 200, f"{path} must be public"
    assert response.content == branding.icon_bytes()
    assert response.headers["content-type"].startswith(branding.icon_media_type())
