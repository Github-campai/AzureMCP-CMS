"""JWT validation for Entra ID bearer tokens + hybrid allowlist check.

Implements the OAuth 2.1 Resource Server pattern from MCP spec 2025-03-26.
"""

import json
import logging
import threading
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

#: Single-flight guard. Validation runs in a worker thread (see
#: `EntraAuthMiddleware`), so a burst of requests arriving on a cold or expired
#: cache would otherwise each fire their own fetch. The first one through the
#: lock does the work; the rest find it cached and walk on.
_JWKS_LOCK = threading.Lock()


def _fetch_jwks_raw() -> list[dict[str, Any]]:
    cached = _fresh_jwks()
    if cached is not None:
        return cached
    with _JWKS_LOCK:
        # Re-check: another thread may have filled the cache while we waited.
        cached = _fresh_jwks()
        if cached is not None:
            return cached
        response = httpx.get(_JWKS_URL, timeout=10)
        response.raise_for_status()
        keys = response.json().get("keys", [])
        _JWKS_CACHE["keys"] = keys
        _JWKS_CACHE["fetched_at"] = time.time()
        return keys


def _fresh_jwks() -> list[dict[str, Any]] | None:
    cached = _JWKS_CACHE.get("keys")
    if cached and time.time() - _JWKS_CACHE["fetched_at"] < _JWKS_TTL_SECONDS:
        return cached
    return None


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
    # tokens) OR the `admcms.invoke` app role (roles claim — service-principal /
    # client-credentials tokens). The app-role value must differ from the delegated
    # scope value (Entra forbids duplicate values across scopes and app roles).
    # `or ""`/`or []` so explicit nulls don't crash.
    scp = claims.get("scp") or ""
    scopes = scp.split() if isinstance(scp, str) else scp
    roles = claims.get("roles") or []
    if not isinstance(roles, list):
        roles = [roles]
    if "admcms.read" not in scopes and "admcms.invoke" not in roles:
        raise InvalidToken(
            f"Missing required scope admcms.read / role admcms.invoke; "
            f"got scopes={scopes} roles={roles}"
        )

    return claims


_ALLOWLIST_CACHE: dict[str, Any] = {"data": None, "fetched_at": 0.0}
_ALLOWLIST_TTL_SECONDS = 300

#: Same single-flight reasoning as the JWKS lock, and it bites harder here: the
#: TTL is five minutes, so without it every burst that lands on an expired
#: cache sends one Key Vault call per request.
_ALLOWLIST_LOCK = threading.Lock()


def _load_allowlist() -> dict[str, list[str]]:
    cached = _fresh_allowlist()
    if cached is not None:
        return cached
    with _ALLOWLIST_LOCK:
        cached = _fresh_allowlist()
        if cached is not None:
            return cached
        return _refresh_allowlist()


def _fresh_allowlist() -> dict[str, list[str]] | None:
    cached = _ALLOWLIST_CACHE.get("data")
    if cached and time.time() - _ALLOWLIST_CACHE["fetched_at"] < _ALLOWLIST_TTL_SECONDS:
        return cached
    return None


def _refresh_allowlist() -> dict[str, list[str]]:
    now = time.time()
    raw = get_secret("admcms-allowlist")
    data = json.loads(raw) if raw else {"users": [], "groups": []}
    data.setdefault("users", [])
    data.setdefault("groups", [])
    _ALLOWLIST_CACHE["data"] = data
    _ALLOWLIST_CACHE["fetched_at"] = now
    return data


def has_group_overage(claims: dict[str, Any]) -> bool:
    """True when Entra left the `groups` claim out because there were too many.

    Past roughly 200 group memberships Entra stops embedding them and puts a
    `_claim_names` / `hasgroups` pointer in the token instead, expecting the
    resource to fetch the list from Graph. Nothing here does that, so such a
    user silently falls back to the per-user allowlist — and an administrator
    who only had access through a group is refused with a message that explains
    nothing. Detecting it does not fix the membership, but it turns a mystery
    into a sentence someone can act on.
    """
    if claims.get("hasgroups"):
        return True
    claim_names = claims.get("_claim_names") or {}
    return isinstance(claim_names, dict) and "groups" in claim_names


def _overage_note(claims: dict[str, Any]) -> str:
    """Extra sentence for a denial when the groups claim went missing."""
    if not has_group_overage(claims):
        return ""
    return (
        " Your token carries no group list because you are a member of too many groups "
        "(Entra group overage), so group-based access could not be evaluated. Ask an "
        "administrator to add you to the allowlist by user id."
    )


def check_access(claims: dict[str, Any]) -> None:
    """Raise Forbidden unless the claim subject is on the allowlist.

    Order: (1) Campai group membership, (2) Key Vault JSON allowlist.
    Tenant check already happened in validate_jwt.
    """
    settings = get_settings()
    oid = claims.get("oid", "")
    groups = claims.get("groups") or []
    upn = claims.get("upn", "")

    if has_group_overage(claims):
        logger.warning(
            "Token has a groups overage (no group list); only the user allowlist applies. "
            "upn=%s oid=%s",
            upn,
            oid,
        )

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
    raise Forbidden("User not on allowlist." + _overage_note(claims))


def check_admin_access(claims: dict[str, Any]) -> None:
    """Raise Forbidden unless the caller may read /admin/*.

    Being on the allowlist is not enough. The admin endpoints expose the audit
    trail of *every* user -- their UPN and the SQL their questions produced -- so
    a customer end-user with data access would otherwise be able to read what
    Campai has been querying.

    Configured under an `admins` key in the same Key Vault allowlist:

        {"users": [...], "groups": [...],
         "admins": {"users": [<oid>], "groups": [<group-id>]}}

    Fails closed: no `admins` configuration means nobody gets in. An empty
    section locking out the admin UI is recoverable; silently granting every
    authorised user access to everyone else's audit trail is not.
    """
    oid = claims.get("oid", "")
    groups = claims.get("groups") or []
    upn = claims.get("upn", "")

    if has_group_overage(claims):
        logger.warning(
            "Admin check on a token with a groups overage; only the admin user list "
            "applies. upn=%s oid=%s",
            upn,
            oid,
        )

    try:
        allowlist = _load_allowlist()
    except Exception as e:
        logger.exception("Failed to load allowlist from Key Vault")
        raise Forbidden(f"Allowlist unavailable: {e}") from e

    admins = allowlist.get("admins") or {}
    admin_users = admins.get("users") or []
    admin_groups = admins.get("groups") or []

    if not admin_users and not admin_groups:
        logger.warning(
            "No 'admins' section in the allowlist; denying /admin to everyone. upn=%s",
            upn,
        )
        raise Forbidden("No administrators are configured")

    if oid in admin_users:
        logger.info("Admin access granted via user: upn=%s oid=%s", upn, oid)
        return

    if set(groups) & set(admin_groups):
        logger.info("Admin access granted via group: upn=%s oid=%s", upn, oid)
        return

    logger.info("Admin access denied: upn=%s oid=%s", upn, oid)
    raise Forbidden("Not an administrator." + _overage_note(claims))
