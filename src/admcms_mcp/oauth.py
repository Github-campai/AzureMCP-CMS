"""OAuth 2.1 discovery + Entra proxy, so any MCP client connects with just a URL.

Entra ID does not implement RFC 7591 dynamic client registration, and MCP
clients (Claude, Cursor, VS Code, Claude Code) refuse to start an authorization
flow when the advertised authorization server has no `registration_endpoint`.
Pointing `authorization_servers` straight at login.microsoftonline.com therefore
dead-ends every client that only gets a URL from the user.

This module makes the MCP server itself the *advertised* authorization server:

- RFC 9728 protected-resource metadata naming `/mcp` as the canonical resource
- RFC 8414 authorization-server metadata pointing at our own three endpoints
- `/register` — DCR stub returning the pre-registered Entra app id
- `/authorize` — 302 to Entra with the scope rewritten to the form Entra wants
- `/token` — server-side proxy of the code/refresh exchange to Entra

Entra remains the only identity provider; nothing is minted here. Because the
token exchange runs server-side without a client secret, the Entra app
registration must carry the client redirect URIs under *publicClient*
("Mobile and desktop applications") — see docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from admcms_mcp.config import get_settings

logger = logging.getLogger(__name__)

#: Scope value we advertise to clients. Rewritten to the fully-qualified Entra
#: form (`api://<app-id>/admcms.read`) before any request reaches Entra.
SCOPE_SHORT = "admcms.read"

#: Scopes that Entra understands verbatim and that must survive the rewrite.
_PASSTHROUGH_SCOPES = frozenset({"openid", "profile", "email", "offline_access"})

#: Paths this module serves. `server.py` adds these to the auth middleware's
#: public set — an unauthenticated client must be able to discover how to
#: authenticate, and must be able to register and obtain a token.
PUBLIC_PATHS = frozenset(
    {
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
        "/register",
        "/authorize",
        "/token",
    }
)


def resource_uri(app_id: str) -> str:
    """Entra Application ID URI for an app id, tolerating an already-prefixed value.

    `ENTRA_APP_ID` normally holds the bare GUID, but a deployment that sets the
    identifier URI instead must not end up with `api://api://...`.
    """
    app_id = app_id.strip()
    return app_id if app_id.startswith("api://") else f"api://{app_id}"


def _request_host(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host", "")
    return (forwarded_host.split(",")[0].strip() or request.url.netloc).lower()


def _host_of(url: str) -> str:
    return url.strip().rstrip("/").split("//")[-1].split("/")[0].lower()


def public_base_url(request: Request) -> str:
    """Externally-visible base URL for *this* request, without trailing slash.

    The app answers on more than one hostname: `admcms.campai.nl` and the
    customer's own `mcp-cms-adm.cmsnl.com`. Each has to describe itself, or a
    client that dialled one is handed metadata naming the other -- confusing at
    best, and rejected by clients that check the RFC 9728 `resource` against the
    URL they connected to.

    So if the incoming host is one we recognise (`PUBLIC_BASE_URL` or an entry in
    `PUBLIC_BASE_URL_ALIASES`), that host is echoed back. Anything else falls
    back to the canonical URL, so a spoofed Host header cannot make the server
    advertise someone else's domain.

    With nothing configured this derives from `X-Forwarded-*`: Container Apps
    terminates TLS at the ingress, so uvicorn sees plain HTTP and advertising
    that scheme would make clients reject the metadata.
    """
    try:
        settings = get_settings()
        canonical = settings.public_base_url.strip().rstrip("/")
        aliases = [a.strip().rstrip("/") for a in settings.public_base_url_aliases if a.strip()]
    except Exception:  # settings incomplete (tests / early boot)
        canonical, aliases = "", []

    if canonical:
        known = {_host_of(url): url for url in [canonical, *aliases]}
        return known.get(_request_host(request), canonical)

    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    scheme = forwarded_proto.split(",")[0].strip() or request.url.scheme
    host = _request_host(request)
    # Builds a URL, not an HTML response; there is no Flask view here.
    # nosemgrep: python.flask.security.audit.directly-returned-format-string.directly-returned-format-string
    return f"{scheme}://{host}"


def canonical_resource(request: Request) -> str:
    """The MCP endpoint itself — the resource identifier clients bind tokens to."""
    return f"{public_base_url(request)}/mcp"


def protected_resource_metadata_url(request: Request) -> str:
    """RFC 9728 path-inserted metadata URL for the `/mcp` resource."""
    return f"{public_base_url(request)}/.well-known/oauth-protected-resource/mcp"


def _entra_authority(request_hint: str = "") -> str:
    settings = get_settings()
    authority = (request_hint or settings.entra_authority or "organizations").strip("/")
    return f"https://login.microsoftonline.com/{authority}/oauth2/v2.0"


def _rewrite_scope(raw_scope: str) -> str:
    """Map our advertised short scope onto the form Entra expects.

    Clients that already send the fully-qualified scope are left alone, and
    `offline_access` is added so refresh tokens are issued — without it every
    session dies after roughly an hour with no way to recover silently.
    """
    settings = get_settings()
    full_scope = f"{resource_uri(settings.entra_app_id)}/{SCOPE_SHORT}"

    rewritten: list[str] = []
    for part in raw_scope.split():
        if part in _PASSTHROUGH_SCOPES:
            rewritten.append(part)
        elif part == SCOPE_SHORT or part.endswith(f"/{SCOPE_SHORT}"):
            rewritten.append(full_scope)
        else:
            rewritten.append(part)

    if not rewritten:
        rewritten.append(full_scope)
    if "offline_access" not in rewritten:
        rewritten.append("offline_access")
    return " ".join(dict.fromkeys(rewritten))


async def protected_resource_metadata(request: Request) -> JSONResponse:
    """RFC 9728 — tells the client which authorization server guards `/mcp`."""
    return JSONResponse(
        {
            "resource": canonical_resource(request),
            "resource_name": "ADMCMS MCP",
            "resource_documentation": "https://github.com/Github-campai/AzureMCP-CMS",
            # Our own base URL, not Entra: only we expose a registration_endpoint.
            "authorization_servers": [public_base_url(request)],
            "scopes_supported": [SCOPE_SHORT],
            "bearer_methods_supported": ["header"],
            "resource_signing_alg_values_supported": ["RS256"],
        }
    )


async def authorization_server_metadata(request: Request) -> JSONResponse:
    """RFC 8414 — authorization-code + PKCE, with DCR available."""
    base = public_base_url(request)
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [SCOPE_SHORT, "offline_access"],
            "service_documentation": "https://github.com/Github-campai/AzureMCP-CMS",
        }
    )


async def register_client(request: Request) -> JSONResponse:
    """RFC 7591 stub — hand back the pre-registered Entra app id.

    Every client shares one Entra public client. That is safe because Entra
    still refuses any `redirect_uri` that is not registered on the app, so a
    rogue client cannot redirect an authorization code to itself.
    """
    settings = get_settings()
    if not settings.entra_app_id:
        return JSONResponse(
            {"error": "server_error", "error_description": "ENTRA_APP_ID is not configured"},
            status_code=500,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    requested_redirects = body.get("redirect_uris") or []
    logger.info("DCR stub issued client_id for redirect_uris=%s", requested_redirects)

    return JSONResponse(
        {
            "client_id": settings.entra_app_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": requested_redirects,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": SCOPE_SHORT,
        },
        status_code=201,
    )


async def authorize_proxy(request: Request) -> Response:
    """Redirect to Entra, rewriting the scope and dropping RFC 8707 `resource`.

    The v2.0 endpoint has no `resource` parameter — passing the one MCP clients
    send makes Entra reject the request outright.
    """
    settings = get_settings()
    if not settings.entra_app_id:
        return JSONResponse(
            {"error": "server_error", "error_description": "ENTRA_APP_ID is not configured"},
            status_code=500,
        )

    params = dict(request.query_params)
    params.pop("resource", None)
    params["scope"] = _rewrite_scope(params.get("scope", ""))
    return RedirectResponse(
        url=f"{_entra_authority()}/authorize?{urlencode(params)}", status_code=302
    )


async def token_proxy(request: Request) -> Response:
    """Proxy the code / refresh-token exchange to Entra, unchanged but rescoped."""
    settings = get_settings()
    if not settings.entra_app_id:
        return JSONResponse(
            {"error": "server_error", "error_description": "ENTRA_APP_ID is not configured"},
            status_code=500,
        )

    form = await request.form()
    body = {key: str(value) for key, value in form.items()}
    body.pop("resource", None)
    if "scope" in body:
        body["scope"] = _rewrite_scope(body["scope"])

    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.post(
            f"{_entra_authority()}/token",
            data=body,
            headers={"Accept": "application/json"},
        )

    if upstream.status_code >= 400:
        # Never log `code`, `refresh_token` or any secret the client sent.
        logged = {k: v for k, v in body.items() if k in ("grant_type", "client_id", "scope")}
        # `logged` is whitelisted above; code / refresh_token / client_secret never
        # reach the log.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.warning("Entra token endpoint returned %s for %s", upstream.status_code, logged)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


def build_oauth_routes() -> list[Route]:
    """Routes for discovery, registration and the Entra proxy.

    Both the bare and the `/mcp`-suffixed well-known paths are served: RFC 9728
    tells clients to insert the resource path, while older clients probe the
    bare form.
    """
    return [
        Route(
            "/.well-known/oauth-protected-resource",
            protected_resource_metadata,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            protected_resource_metadata,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-authorization-server",
            authorization_server_metadata,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-authorization-server/mcp",
            authorization_server_metadata,
            methods=["GET"],
        ),
        Route("/register", register_client, methods=["POST"]),
        Route("/authorize", authorize_proxy, methods=["GET"]),
        Route("/token", token_proxy, methods=["POST"]),
    ]
