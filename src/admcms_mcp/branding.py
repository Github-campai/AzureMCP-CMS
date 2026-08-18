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

Replacing the logo means dropping a file in `assets/`. The format is detected
from the bytes rather than the extension: a file saved as WebP but named .png is
a mistake that would otherwise be served with the wrong Content-Type and silently
fail to render.
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
_ASSET_DIR = Path(__file__).resolve().parent / "assets"

#: Checked in order; the first that exists wins. Any of these formats works, so
#: nobody has to convert an image to get a logo on the connector.
_ICON_FILENAMES = ("icon.png", "icon.webp", "icon.jpg", "icon.jpeg", "icon.svg")

#: Route the icon is served on. Kept as .png regardless of the underlying format:
#: it is an opaque URL, and the response carries the real Content-Type.
ICON_ROUTE = "/icon.png"

#: The same image under the paths a client may probe instead of reading
#: `serverInfo.icons`. Claude's connector UI shows a generated avatar despite the
#: icons being published correctly, so the browser conventions are served too --
#: they cost one route each and cover the clients that never look at the metadata.
ICON_ALIAS_ROUTES = ("/favicon.ico", "/apple-touch-icon.png", "/icon.svg")

_FALLBACK_MEDIA_TYPE = "image/png"

#: Beyond this a data URI costs more than it is worth on every initialize.
_MAX_DATA_URI_BYTES = 150_000


@lru_cache(maxsize=1)
def icon_bytes() -> bytes | None:
    """The raw icon, or None when no asset is present.

    A missing icon must never break startup: the server is useful without one.
    """
    for name in _ICON_FILENAMES:
        path = _ASSET_DIR / name
        try:
            if path.is_file():
                return path.read_bytes()
        except OSError as exc:
            logger.warning("Icon not readable at %s: %s", path, exc)
    logger.warning("No icon asset found in %s", _ASSET_DIR)
    return None


def _sniff(data: bytes) -> tuple[str, tuple[int, int] | None]:
    """Media type and pixel size from the file's own header.

    Trusting the extension is how a WebP ends up served as image/png. Returns
    `(media_type, (width, height) | None)`; size is None for formats whose
    header this does not parse (SVG has no pixel size at all).
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return "image/png", (width, height)

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        # VP8X carries the canvas size as two 24-bit values, minus one.
        if data[12:16] == b"VP8X" and len(data) >= 30:
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
            return "image/webp", (width, height)
        # Lossy VP8: size lives after the 3-byte start code in the frame header.
        if data[12:16] == b"VP8 " and len(data) >= 30:
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
            return "image/webp", (width, height)
        return "image/webp", None

    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", None

    stripped = data[:512].lstrip()
    if stripped.startswith(b"<svg") or stripped.startswith(b"<?xml"):
        return "image/svg+xml", None

    if data[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon", None

    logger.warning("Unrecognised icon format; serving as %s", _FALLBACK_MEDIA_TYPE)
    return _FALLBACK_MEDIA_TYPE, None


@lru_cache(maxsize=1)
def icon_media_type() -> str:
    """Content-Type for the icon, detected from its bytes."""
    data = icon_bytes()
    return _sniff(data)[0] if data else _FALLBACK_MEDIA_TYPE


@lru_cache(maxsize=1)
def icon_dimensions() -> str | None:
    """`"<w>x<h>"` read from the file header, or None if not determinable.

    Read from the file rather than hardcoded, so replacing the asset with a
    different size cannot publish a wrong `sizes` hint.
    """
    data = icon_bytes()
    if not data:
        return None
    size = _sniff(data)[1]
    if not size or not all(size):
        return None
    return f"{size[0]}x{size[1]}"


def icon_data_uri() -> str | None:
    """The icon as a `data:` URI, or None when missing or too large."""
    data = icon_bytes()
    if not data or len(data) > _MAX_DATA_URI_BYTES:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{icon_media_type()};base64,{encoded}"


def server_icons(public_base_url: str = "") -> list[dict]:
    """`serverInfo.icons` entries, most preferred first.

    Shaped as plain dicts so this module stays free of MCP imports; FastMCP
    validates them into `Icon` models.
    """
    if icon_bytes() is None:
        return []

    dimensions = icon_dimensions()
    sizes = [dimensions] if dimensions else None
    media_type = icon_media_type()
    icons: list[dict] = []

    base = (public_base_url or "").strip().rstrip("/")
    if base.startswith("https://"):
        icons.append({"src": f"{base}{ICON_ROUTE}", "mimeType": media_type, "sizes": sizes})

    data_uri = icon_data_uri()
    if data_uri:
        icons.append({"src": data_uri, "mimeType": media_type, "sizes": sizes})

    return icons
