"""The icon clients show next to this connector.

MCP carries this in `serverInfo.icons` at `initialize` (an `Icon` is `{src,
mimeType, sizes}`, where `src` is a URL or a data URI). Two entries are
published for the same image:

1. an absolute https URL to `/icon.png` on this server, and
2. the same bytes as a `data:` URI.

Both, because a client either fetches the URL or refuses to make a second
request; the data URI needs no fetch, no public route and no CORS, while the URL
keeps the payload small for clients that prefer it. The URL is only included when
`PUBLIC_BASE_URL` is set, since a relative or http:// icon URL is useless to a
client and worse than absent.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

#: Ships inside the package. A path outside it (repo-root ./assets) resolves
#: during development but not from site-packages in the container, where pip has
#: copied only the package itself.
_ICON_PATH = Path(__file__).resolve().parent / "assets" / "icon.png"

ICON_MEDIA_TYPE = "image/png"
ICON_ROUTE = "/icon.png"

#: Beyond this a data URI costs more than it is worth on every initialize.
_MAX_DATA_URI_BYTES = 150_000


@lru_cache(maxsize=1)
def icon_bytes() -> bytes | None:
    """The raw icon, or None when the asset is missing.

    A missing icon must never break startup: the server is useful without one.
    """
    try:
        return _ICON_PATH.read_bytes()
    except OSError as exc:
        logger.warning("Icon not readable at %s: %s", _ICON_PATH, exc)
        return None


@lru_cache(maxsize=1)
def icon_dimensions() -> str | None:
    """`"<w>x<h>"` read from the PNG header, or None if it cannot be determined.

    Read from the file rather than hardcoded, so replacing the asset with a
    different size does not silently publish a wrong `sizes` hint.
    """
    data = icon_bytes()
    if not data or len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if not width or not height:
        return None
    return f"{width}x{height}"


def icon_data_uri() -> str | None:
    """The icon as a `data:` URI, or None when missing or too large."""
    data = icon_bytes()
    if not data or len(data) > _MAX_DATA_URI_BYTES:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{ICON_MEDIA_TYPE};base64,{encoded}"


def server_icons(public_base_url: str = "") -> list[dict]:
    """`serverInfo.icons` entries, most preferred first.

    Shaped as plain dicts so this module stays free of MCP imports; FastMCP
    validates them into `Icon` models.
    """
    if icon_bytes() is None:
        return []

    sizes = [icon_dimensions()] if icon_dimensions() else None
    icons: list[dict] = []

    base = (public_base_url or "").strip().rstrip("/")
    if base.startswith("https://"):
        icons.append({"src": f"{base}{ICON_ROUTE}", "mimeType": ICON_MEDIA_TYPE, "sizes": sizes})

    data_uri = icon_data_uri()
    if data_uri:
        icons.append({"src": data_uri, "mimeType": ICON_MEDIA_TYPE, "sizes": sizes})

    return icons
