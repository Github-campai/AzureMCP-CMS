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

Both proxied endpoints only broker what this server actually advertises: the
`client_id` must be the one `/register` hands out, and `/token` accepts only the
grant types listed in `authorization_server_metadata()`. Anything else is a 400
here instead of a request carrying our hostname to Entra.

`redirect_uri` is deliberately *not* checked against a list. Entra already
refuses any redirect that is not registered on the app, and the set of MCP
client callbacks is open-ended (`https://claude.ai/...`, `vscode://...`,
`cursor://...`, `http://127.0.0.1:<random port>/`), so a local allowlist would
break new clients without adding a guarantee Entra does not already give.
"""

from __future__ import annotations

import logging
import re
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

#: Grant types the token proxy forwards. `authorization_server_metadata()`
#: advertises exactly these two, so anything else is a client that either
#: misread the metadata or is probing — refuse it here rather than let Entra
#: decide what our proxy is willing to broker.
ALLOWED_GRANT_TYPES = frozenset({"authorization_code", "refresh_token"})

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


#: Authority values that are not a tenant id but mean something to Entra.
_AUTHORITY_KEYWORDS = frozenset({"organizations", "common", "consumers"})

#: A tenant id, or one of the keywords above. Anything else is a typo in the
#: configuration, and putting it in the URL would send users to a sign-in page
#: that cannot exist.
_AUTHORITY_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _valid_authority(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in _AUTHORITY_KEYWORDS or bool(_AUTHORITY_PATTERN.match(lowered))


def authority_for_request(request: Request) -> str:
    """Which Entra directory this request's hostname signs people in to.

    The authority is the segment in the path of the Entra sign-in URL, and it
    decides *which directory* authenticates the user. `organizations` means "any
    work tenant — look up where this person lives", which for a guest is his own
    employer's tenant rather than the one he is a guest in. His token then
    carries that tenant, `validate_jwt` refuses it, and his membership of a group
    in the customer tenant never gets a chance to matter.

    Naming the tenant in the path instead signs him in to *that* directory, guest
    object and group memberships included. It is what Power BI does when it hands
    out a tenant-scoped link, and why a guest gets into Power BI but not here.

    Keyed on the hostname because the app answers on more than one: the customer
    hostname belongs to the customer tenant, ours does not. A single global
    setting would push one party out of its own directory.

    The host comes from the request and is therefore caller-controlled, but it is
    only ever a *lookup key* into configuration — never interpolated into the
    URL. A forged Host header can at most select an authority an administrator
    already configured, and `validate_jwt` still checks the tenant afterwards.
    """
    try:
        settings = get_settings()
    except Exception:  # settings incomplete (tests / early boot)
        return ""

    configured = settings.entra_authority_by_host.get(_request_host(request))
    if not configured:
        return ""
    if not _valid_authority(configured):
        logger.warning(
            "ENTRA_AUTHORITY_BY_HOST holds %r, which is not a tenant id or a known "
            "keyword; falling back to ENTRA_AUTHORITY",
            configured[:60],
        )
        return ""
    return configured


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


def _bad_request(description: str, error: str = "invalid_request") -> JSONResponse:
    """RFC 6749 §5.2 error body."""
    return JSONResponse({"error": error, "error_description": description}, status_code=400)


def _reject_foreign_client(params: dict[str, str]) -> JSONResponse | None:
    """Refuse a `client_id` other than the one `/register` hands out.

    Without this check `/authorize` is an open relay: anyone can craft a link on
    our own domain that carries the user to Entra and asks consent for *their*
    application, borrowing our hostname's credibility. The same applies to
    `/token`, where a foreign client_id would have us broker an exchange for an
    app we know nothing about.

    Deliberately a refusal rather than a silent overwrite: a client sending a
    different id has a configuration problem, and quietly correcting it would
    hide that from whoever has to debug the connector.
    """
    settings = get_settings()
    expected = settings.entra_app_id.strip()
    supplied = (params.get("client_id") or "").strip()

    if not supplied:
        return _bad_request("client_id is required")
    if supplied not in {expected, resource_uri(expected)}:
        # Client-controlled value: truncated and repr'd so it cannot forge log lines.
        logger.warning("Refused proxy request with unknown client_id=%r", supplied[:80])
        return _bad_request("client_id is not registered with this authorization server")
    return None


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
    refusal = _reject_foreign_client(params)
    if refusal is not None:
        return refusal

    params.pop("resource", None)
    params["scope"] = _rewrite_scope(params.get("scope", ""))
    # Same authority as `token_proxy` derives, or the code would be redeemed at
    # a different directory than it was issued by and every sign-in would break.
    authority = _entra_authority(authority_for_request(request))
    return RedirectResponse(url=f"{authority}/authorize?{urlencode(params)}", status_code=302)


def _entra_error_summary(response: httpx.Response) -> str:
    """Name the reason Entra refused, in one loggable line.

    Without this a refusal reads "Entra token endpoint returned 400" and the
    body — the only place the reason lives — is handed to the client and
    forgotten. A failing `refresh_token` grant does not show up in the Entra
    sign-in logs either, so there was no second copy to fall back on: the
    diagnosis had to start from a status code alone.

    Only the OAuth error envelope is logged (`error`, `error_codes`, the AADSTS
    text, and the correlation id Microsoft support asks for). Never a token: the
    response to a *failed* exchange carries none, and the fields are named
    explicitly rather than dumped, so a future Entra field cannot leak in.
    """
    try:
        payload = response.json()
    except ValueError:
        return f"non-JSON body ({response.text[:120]!r})"

    if not isinstance(payload, dict):
        return "unexpected body shape"

    parts = [
        f"error={payload.get('error')!r}",
        f"error_codes={payload.get('error_codes')}",
    ]
    description = (str(payload.get("error_description") or "").splitlines() or [""])[0]
    if description:
        parts.append(f"description={description[:200]!r}")
    correlation_id = payload.get("correlation_id")
    if correlation_id:
        parts.append(f"correlation_id={correlation_id}")
    return " ".join(parts)


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

    grant_type = body.get("grant_type", "").strip()
    if grant_type not in ALLOWED_GRANT_TYPES:
        logger.warning("Refused token request with grant_type=%r", grant_type[:40])
        return _bad_request(
            f"grant_type must be one of {sorted(ALLOWED_GRANT_TYPES)}",
            error="unsupported_grant_type",
        )

    refusal = _reject_foreign_client(body)
    if refusal is not None:
        return refusal

    body.pop("resource", None)
    if "scope" in body:
        body["scope"] = _rewrite_scope(body["scope"])

    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.post(
            f"{_entra_authority(authority_for_request(request))}/token",
            data=body,
            headers={"Accept": "application/json"},
        )

    if upstream.status_code >= 400:
        # Never log `code`, `refresh_token` or any secret the client sent.
        logged = {k: v for k, v in body.items() if k in ("grant_type", "client_id", "scope")}
        # `logged` is whitelisted above; code / refresh_token / client_secret never
        # reach the log.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.warning(
            "Entra token endpoint returned %s for %s: %s",
            upstream.status_code,
            logged,
            _entra_error_summary(upstream),
        )

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
