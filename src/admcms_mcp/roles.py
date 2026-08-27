"""Role-based access to the tools.

Being on the allowlist decides *whether* someone gets in; a role decides *what*
they can ask once inside. A financial manager has no business running free-form
SQL, and an IT manager does not need the margin report to do their job.

Roles arrive in the Entra access token as the `roles` claim, filled by the app
roles assigned in the app registration (`admcms.finance`, `admcms.it`, ...).
Deliberately not group membership: past roughly 200 groups Entra replaces the
`groups` claim with an overage pointer (see `auth.has_group_overage`), and a
user would silently lose their role. App roles are unaffected by that.

`admcms.invoke` already lives in the same claim for service principals; it is
not a role in this sense and is ignored here.

Enforcement happens in two places:

- `server.RoleFilteredMCP.list_tools` hides what the caller may not call, so the
  model never sees it — and `describe_server` follows automatically, because it
  reads the same list.
- `server._audited` refuses the call itself. Hiding a tool is not access
  control; this is, and the refusal lands in the audit log.

Admin access to `/admin/*` is *not* handled here. That runs on the `admins`
section of the Key Vault allowlist (`auth.check_admin_access`) and stays there:
one mechanism for one decision.

Rollout is staged through `ROLE_ENFORCEMENT`:

    off      no checks at all (behaviour before this module existed)
    warn     everything is allowed, would-be refusals are logged (default)
    enforce  refusals are real

Start on `warn`, assign the app roles in Entra, read the logs for a few days,
then switch to `enforce`. That order matters: an app role that nobody has
assigned yet would otherwise lock out every user at once.
"""

from __future__ import annotations

import contextvars
import logging

from admcms_mcp.config import get_settings

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_FINANCE = "finance"
ROLE_IT = "it"
ROLE_SALES = "sales"
ROLE_VIEWER = "viewer"

#: Available to every role. Three of these describe the server rather than the
#: business (`describe_server`, `health_check`, `get_schema`), and the other
#: three are what you need before you can ask anything sensible at all.
_BASE_TOOLS = frozenset(
    {
        "describe_server",
        "health_check",
        "get_schema",
        "get_lookups",
        "search_products",
        "get_filter_options",
    }
)

_FINANCE_TOOLS = frozenset(
    {
        "get_revenue",
        "get_revenue_by_business_unit",
        "get_margin_report",
        "get_kpi_summary",
        "get_country_segment_matrix",
        "get_top_customers",
        "get_shipping_summary",
        "get_shipping_cost_analysis",
        "get_supplier_purchases",
        "get_credit_note_analysis",
        "get_payment_method_stats",
        "get_salesrep_performance",
        "get_discount_analysis",
        "get_order_origin_mix",
        "get_customer_orders",
        "get_stock_levels",
        "get_dead_stock",
    }
)

_SALES_TOOLS = frozenset(
    {
        "get_revenue",
        "get_top_customers",
        "get_customer_orders",
        "get_stock_levels",
        "get_dead_stock",
        "get_order_origin_mix",
        "get_salesrep_performance",
        "get_country_segment_matrix",
    }
)

#: The technical side: free-form SQL, the data-quality check, and the stock and
#: order tools that questions about the data itself tend to need. No turnover,
#: no margins, no discounts — an IT manager troubleshooting a sync does not need
#: to know what the company earned.
_IT_TOOLS = frozenset(
    {
        "query_database",
        "detect_price_anomalies",
        "get_stock_levels",
        "get_dead_stock",
        "get_customer_orders",
    }
)

#: The floor: what someone without an assigned app role gets.
_VIEWER_TOOLS = frozenset({"get_stock_levels"})

ROLE_TOOLS: dict[str, frozenset[str]] = {
    ROLE_ADMIN: frozenset({"*"}),
    ROLE_FINANCE: _BASE_TOOLS | _FINANCE_TOOLS,
    ROLE_SALES: _BASE_TOOLS | _SALES_TOOLS,
    ROLE_IT: _BASE_TOOLS | _IT_TOOLS,
    ROLE_VIEWER: _BASE_TOOLS | _VIEWER_TOOLS,
}

KNOWN_ROLES = frozenset(ROLE_TOOLS)

#: Set by the auth middleware, read by `list_tools` and `_audited`.
current_roles: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "admcms_mcp_current_roles", default=frozenset()
)


class RoleDenied(PermissionError):  # noqa: N818 — HTTP-semantic name (403)
    """Raised when the caller's roles do not cover the requested tool."""


def _normalise(raw: str) -> str | None:
    """Map an app-role claim value onto a role name, or None if unknown.

    Accepts `admcms.finance`, `Finance`, `admcms-finance`: whatever the last
    segment is, lowercased. Keeps the Entra side free to use whichever naming
    convention reads best there.
    """
    value = raw.strip().lower().replace("-", ".").replace("_", ".")
    if not value:
        return None
    candidate = value.rsplit(".", 1)[-1]
    return candidate if candidate in KNOWN_ROLES else None


def roles_from_claims(claims: dict) -> frozenset[str]:
    """Read the caller's roles from validated JWT claims.

    Unknown values are logged and ignored — `admcms.invoke` lands here too, and
    that is fine. A caller with no recognised role falls back to `ROLE_DEFAULT`,
    so access is never accidentally total.
    """
    settings = get_settings()
    raw = claims.get(settings.role_claim) or []
    if isinstance(raw, str):
        raw = raw.split()

    roles: set[str] = set()
    unknown: list[str] = []
    for item in raw:
        role = _normalise(str(item))
        if role:
            roles.add(role)
        else:
            unknown.append(str(item))

    if unknown:
        logger.debug(
            "Ignoring role claim values that are not roles for upn=%s: %s",
            claims.get("upn") or claims.get("oid"),
            unknown,
        )

    if not roles:
        fallback = settings.role_default.strip().lower()
        if fallback in KNOWN_ROLES:
            logger.info(
                "No app role assigned for upn=%s; falling back to %s",
                claims.get("upn") or claims.get("oid"),
                fallback,
            )
            roles.add(fallback)

    return frozenset(roles)


def allowed_tools(roles: frozenset[str]) -> frozenset[str] | None:
    """Tool names these roles may call, or None when unrestricted."""
    if ROLE_ADMIN in roles:
        return None
    allowed: set[str] = set()
    for role in roles:
        allowed |= ROLE_TOOLS.get(role, frozenset())
    return frozenset(allowed)


def can_call(tool_name: str, roles: frozenset[str] | None = None) -> bool:
    """Whether `roles` (default: the current caller's) cover `tool_name`."""
    effective = current_roles.get() if roles is None else roles
    permitted = allowed_tools(effective)
    return permitted is None or tool_name in permitted


def enforcement() -> str:
    """Current mode: 'off', 'warn' or 'enforce'."""
    mode = get_settings().role_enforcement.strip().lower()
    return mode if mode in ("off", "warn", "enforce") else "warn"


def require_tool(tool_name: str) -> None:
    """Raise RoleDenied when the caller may not call `tool_name`.

    Honours the enforcement mode, so a half-finished Entra configuration cannot
    lock everyone out halfway through the rollout.
    """
    mode = enforcement()
    if mode == "off" or can_call(tool_name):
        return

    roles = sorted(current_roles.get()) or ["<geen>"]
    if mode == "warn":
        logger.warning(
            "Role check (warn-only): tool=%s would be refused for roles=%s", tool_name, roles
        )
        return

    logger.info("Role check: refused tool=%s for roles=%s", tool_name, roles)
    raise RoleDenied(
        f"De tool '{tool_name}' hoort niet bij je rol ({', '.join(roles)}). "
        f"Vraag een beheerder om de juiste app-rol in Entra als je deze nodig hebt."
    )


def describe() -> dict[str, list[str]]:
    """Role → tools, for `describe_server` and the documentation."""
    return {
        role: ["*"] if tools == frozenset({"*"}) else sorted(tools)
        for role, tools in ROLE_TOOLS.items()
    }
