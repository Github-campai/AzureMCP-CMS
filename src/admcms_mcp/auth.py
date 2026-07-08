"""JWT validation for Entra ID bearer tokens + hybrid allowlist check.

Implements the OAuth 2.1 Resource Server pattern from MCP spec 2025-03-26.
"""

import json
import logging
import time
from typing import Any

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError

from admcms_mcp.config import get_settings
from admcms_mcp.keyvault import get_secret

logger = logging.getLogger(__name__)


class InvalidToken(Exception):  # noqa: N818 — HTTP-semantic name (401)
    """Raised when a JWT fails validation."""


class Forbidden(Exception):  # noqa: N818 — HTTP-semantic name (403)
    """Raised when a valid token does not meet allowlist requirements."""


_JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 24 * 3600


def _fetch_jwks_raw() -> list[dict[str, Any]]:
    now = time.time()
    cached = _JWKS_CACHE.get("keys")
    if cached and now - _JWKS_CACHE["fetched_at"] < _JWKS_TTL_SECONDS:
        return cached
    response = httpx.get(_JWKS_URL, timeout=10)
    response.raise_for_status()
    keys = response.json().get("keys", [])
    _JWKS_CACHE["keys"] = keys
    _JWKS_CACHE["fetched_at"] = now
    return keys


def _get_key_for_kid(kid: str) -> Any:
    for jwk in _fetch_jwks_raw():
        if jwk.get("kid") == kid:
            return JsonWebKey.import_key(jwk)
    # Force refresh once on miss, in case of key rotation
    _JWKS_CACHE["fetched_at"] = 0
    for jwk in _fetch_jwks_raw():
        if jwk.get("kid") == kid:
            return JsonWebKey.import_key(jwk)
    raise InvalidToken(f"Unknown signing key: kid={kid}")


def validate_jwt(token: str) -> dict[str, Any]:
    """Validate signature, expiration, audience, issuer, scope. Return claims."""
    settings = get_settings()

    try:
        # authlib callable key receives (header, payload) — extract kid from header
        def _load_key(header: dict, payload: dict) -> Any:  # noqa: ANN001
            return _get_key_for_kid(header["kid"])

        unverified = JsonWebToken(["RS256"]).decode(
            token,
            key=_load_key,
        )
    except JoseError as e:
        raise InvalidToken(f"Invalid token signature: {e}") from e
    except Exception as e:  # malformed
        raise InvalidToken(f"Malformed token: {e}") from e

    claims = dict(unverified)

    # exp
    exp = claims.get("exp")
    if not exp or exp < time.time():
        raise InvalidToken("Token is expired")

    # aud — accept both v2 bare-GUID form and v1 `api://<guid>` form
    expected_auds = {settings.entra_app_id, f"api://{settings.entra_app_id}"}
    aud = claims.get("aud")
    aud_values = set(aud) if isinstance(aud, list) else {aud}
    if not (aud_values & expected_auds):
        raise InvalidToken(f"Wrong audience: got {aud}, expected one of {expected_auds}")

    # iss (must exactly match one of the allowed tenant endpoint forms)
    iss = claims.get("iss", "")
    tid = claims.get("tid", "")
    if tid not in settings.entra_allowed_tenants:
        raise InvalidToken(f"Untrusted tenant: {tid}")
    expected_iss_v1 = f"https://login.microsoftonline.com/{tid}/v1.0"
    expected_iss_v2 = f"https://sts.windows.net/{tid}/"
    expected_iss_v2_alt = f"https://login.microsoftonline.com/{tid}/v2.0"
    if iss not in (expected_iss_v1, expected_iss_v2, expected_iss_v2_alt):
        raise InvalidToken(f"Issuer does not match tenant: {iss}")

    # Authorization: accept the delegated scope `admcms.read` (scp claim — user/OBO
    # tokens) OR the `admcms.read` app role (roles claim — service-principal /
    # client-credentials tokens). `or ""`/`or []` so explicit nulls don't crash.
    scp = claims.get("scp") or ""
    scopes = scp.split() if isinstance(scp, str) else scp
    roles = claims.get("roles") or []
    if not isinstance(roles, list):
        roles = [roles]
    if "admcms.read" not in scopes and "admcms.read" not in roles:
        raise InvalidToken(
            f"Missing required scope/role admcms.read; got scopes={scopes} roles={roles}"
        )

    return claims


_ALLOWLIST_CACHE: dict[str, Any] = {"data": None, "fetched_at": 0.0}
_ALLOWLIST_TTL_SECONDS = 300


def _load_allowlist() -> dict[str, list[str]]:
    now = time.time()
    cached = _ALLOWLIST_CACHE.get("data")
    if cached and now - _ALLOWLIST_CACHE["fetched_at"] < _ALLOWLIST_TTL_SECONDS:
        return cached
    raw = get_secret("admcms-allowlist")
    data = json.loads(raw) if raw else {"users": [], "groups": []}
    data.setdefault("users", [])
    data.setdefault("groups", [])
    _ALLOWLIST_CACHE["data"] = data
    _ALLOWLIST_CACHE["fetched_at"] = now
    return data


def check_access(claims: dict[str, Any]) -> None:
    """Raise Forbidden unless the claim subject is on the allowlist.

    Order: (1) Campai group membership, (2) Key Vault JSON allowlist.
    Tenant check already happened in validate_jwt.
    """
    settings = get_settings()
    oid = claims.get("oid", "")
    groups = claims.get("groups") or []
    upn = claims.get("upn", "")

    # Campai group allowlist (Entra security group in Campai tenant)
    if settings.campai_allowed_group and settings.campai_allowed_group in groups:
        logger.info("Access granted via Campai group: upn=%s oid=%s", upn, oid)
        return

    # Key Vault allowlist
    try:
        allowlist = _load_allowlist()
    except Exception as e:
        logger.exception("Failed to load allowlist from Key Vault")
        raise Forbidden(f"Allowlist unavailable: {e}") from e

    if oid in allowlist["users"]:
        logger.info("Access granted via user allowlist: upn=%s oid=%s", upn, oid)
        return

    if set(groups) & set(allowlist["groups"]):
        logger.info("Access granted via group allowlist: upn=%s oid=%s", upn, oid)
        return

    logger.info("Access denied: upn=%s oid=%s tid=%s", upn, oid, claims.get("tid"))
    raise Forbidden("User not on allowlist")
