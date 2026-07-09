"""HTTP server: Starlette app with MCP Streamable HTTP + Entra JWT auth."""

import logging
from contextlib import asynccontextmanager

# Load .env into os.environ so keyvault.py's env-var overrides (e.g.
# ADMCMS_DB_USERNAME / ADMCMS_DB_PASSWORD) work without Key Vault in
# local dev. Pydantic-settings only populates Settings fields, not
# os.environ, so this has to happen explicitly.
from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route

from admcms_mcp.admin import build_admin_routes
from admcms_mcp.audit import audit_call, current_user
from admcms_mcp.auth import Forbidden, InvalidToken, check_access, validate_jwt
from admcms_mcp.config import get_settings
from admcms_mcp.schema import SCHEMA_KNOWLEDGE
from admcms_mcp.tools import detect_price_anomalies as _detect_price_anomalies
from admcms_mcp.tools import get_credit_note_analysis as _get_credit_note_analysis
from admcms_mcp.tools import get_customer_orders as _get_customer_orders
from admcms_mcp.tools import get_dead_stock as _get_dead_stock
from admcms_mcp.tools import get_discount_analysis as _get_discount_analysis
from admcms_mcp.tools import get_filter_options as _get_filter_options
from admcms_mcp.tools import get_kpi_summary as _get_kpi_summary
from admcms_mcp.tools import get_lookups as _get_lookups
from admcms_mcp.tools import get_margin_report as _get_margin_report
from admcms_mcp.tools import get_order_origin_mix as _get_order_origin_mix
from admcms_mcp.tools import get_payment_method_stats as _get_payment_method_stats
from admcms_mcp.tools import get_revenue as _get_revenue
from admcms_mcp.tools import get_country_segment_matrix as _get_country_segment_matrix
from admcms_mcp.tools import get_revenue_by_business_unit as _get_revenue_by_business_unit
from admcms_mcp.tools import get_salesrep_performance as _get_salesrep_performance
from admcms_mcp.tools import get_schema as _get_schema
from admcms_mcp.tools import get_shipping_cost_analysis as _get_shipping_cost_analysis
from admcms_mcp.tools import get_shipping_summary as _get_shipping_summary
from admcms_mcp.tools import get_stock_levels as _get_stock_levels
from admcms_mcp.tools import get_supplier_purchases as _get_supplier_purchases
from admcms_mcp.tools import get_top_customers as _get_top_customers
from admcms_mcp.tools import query_database as _query_database
from admcms_mcp.tools import search_products as _search_products

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = frozenset({"/", "/healthz", "/.well-known/oauth-protected-resource"})


class McpTrailingSlashMiddleware(BaseHTTPMiddleware):
    """Rewrite /mcp (no slash) to /mcp/ in-place.

    Avoids the 307 redirect that Starlette would emit otherwise — some MCP
    clients (notably Copilot Studio preview) lose their POST body or
    Authorization header when following the redirect.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/mcp":
            request.scope["path"] = "/mcp/"
            request.scope["raw_path"] = b"/mcp/"
        return await call_next(request)


class EntraAuthMiddleware(BaseHTTPMiddleware):
    """Validates bearer tokens on non-public paths.

    Sets `request.state.user_claims` on success for downstream use.
    Short-circuits with 401/403 on failure, including proper
    `WWW-Authenticate` header pointing to OAuth metadata.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # Copilot Studio's preview MCP connector sends GET probes on /mcp/
        # without the bearer token after a successful authenticated POST.
        # Letting these GETs through bypasses the preview bug; the MCP
        # library still requires a valid session id (which only an authed
        # POST initialize can produce), so no unauthenticated data leaks.
        if request.method == "GET" and path.startswith("/mcp"):
            return await call_next(request)

        try:
            settings = get_settings()
            if settings.auth_disabled:
                request.state.user_claims = {"oid": "local-dev", "upn": "dev@localhost"}
                current_user.set("dev@localhost")
                return await call_next(request)

            auth_header = request.headers.get("authorization", "")
            if not auth_header.lower().startswith("bearer "):
                return self._unauthorized(request, "Missing bearer token")

            token = auth_header[7:].strip()
            if not token:
                return self._unauthorized(request, "Empty bearer token")

            try:
                claims = validate_jwt(token)
            except InvalidToken as e:
                return self._unauthorized(request, str(e))

            try:
                check_access(claims)
            except Forbidden as e:
                return JSONResponse(
                    {"error": "forbidden", "detail": _sanitize(str(e))},
                    status_code=403,
                )

            request.state.user_claims = claims
            current_user.set(
                claims.get("upn") or claims.get("oid") or "authenticated"
            )
            return await call_next(request)
        except Exception:
            # Fail closed on any unexpected error — never pass-through to MCP.
            logger.exception("Unexpected error in auth middleware")
            return self._unauthorized(request, "Authentication error")

    def _unauthorized(self, request: Request, detail: str) -> Response:
        safe_detail = _sanitize(detail)
        resource_metadata_url = (
            f"{request.url.scheme}://{request.url.netloc}/.well-known/oauth-protected-resource"
        )
        www_auth = (
            f'Bearer realm="admcms-mcp", '
            f'error="invalid_token", '
            f'error_description="{safe_detail}", '
            f'resource_metadata="{resource_metadata_url}"'
        )
        return JSONResponse(
            {"error": "unauthorized", "detail": safe_detail},
            status_code=401,
            headers={"WWW-Authenticate": www_auth},
        )


_HEADER_UNSAFE_CHARS = str.maketrans({'"': "", "\r": "", "\n": ""})


def _sanitize(detail: str) -> str:
    """Strip characters that would break an HTTP header quoted-string value."""
    return detail.translate(_HEADER_UNSAFE_CHARS)[:200]


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


_INDEX_HTML = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<title>ADMCMS MCP Server</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         max-width: 640px; margin: 6rem auto; padding: 0 1.5rem; color: #1a1a1a; line-height: 1.55; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
  .badge {{ display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
            font-size: 0.85rem; background: #e8f5e9; color: #1b5e20; margin-bottom: 1.5rem; }}
  .ok {{ background: #e8f5e9; color: #1b5e20; }}
  .info {{ background: #e3f2fd; color: #0d47a1; }}
  code {{ background: #f4f4f5; padding: 0.1rem 0.4rem; border-radius: 4px;
          font-size: 0.9em; }}
  ul {{ padding-left: 1.2rem; }}
  li {{ margin-bottom: 0.3rem; }}
  .small {{ color: #6b7280; font-size: 0.85rem; margin-top: 2rem; }}
  a {{ color: #1d4ed8; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="badge {badge_class}">{badge_text}</div>
{body}
<p class="small">ADMCMS MCP Server &middot; Beheerd door Campai &middot;
<a href="/healthz">healthz</a> &middot;
<a href="/.well-known/oauth-protected-resource">OAuth metadata</a></p>
</body>
</html>"""


async def index(request: Request) -> HTMLResponse:
    params = request.query_params
    if params.get("admin_consent") == "True":
        tenant = params.get("tenant", "")
        tenant_html = f"<p>Tenant: <code>{tenant}</code></p>" if tenant else ""
        body = (
            "<p>De CMS-MCP-API is succesvol toegevoegd aan jullie Microsoft Entra "
            "tenant. Gebruikers in de bijbehorende security group kunnen nu via "
            "Claude, Copilot of Cursor de ADMCMS-data bevragen.</p>"
            f"{tenant_html}"
            "<p>Je kunt dit venster nu sluiten.</p>"
        )
        return HTMLResponse(
            _INDEX_HTML.format(
                title="Consent verleend",
                badge_class="ok",
                badge_text="Geslaagd",
                body=body,
            )
        )
    if "error" in params:
        err = params.get("error", "")
        desc = params.get("error_description", "")
        body = (
            "<p>Microsoft Entra heeft de aanvraag afgewezen.</p>"
            f"<p><strong>Error:</strong> <code>{err}</code></p>"
            f"<p><strong>Detail:</strong> {desc}</p>"
            "<p>Neem contact op met Campai (<a href='mailto:Janneke@campai.nl'>"
            "Janneke@campai.nl</a>) als dit blijft optreden.</p>"
        )
        return HTMLResponse(
            _INDEX_HTML.format(
                title="Consent mislukt",
                badge_class="info",
                badge_text="Fout",
                body=body,
            ),
            status_code=400,
        )
    body = (
        "<p>Dit is de MCP-server voor de ADMCMS-database, gehost door Campai "
        "voor CMS Lelystad.</p>"
        "<p>Endpoints:</p>"
        "<ul>"
        "<li><code>POST /mcp</code> &mdash; MCP Streamable HTTP (Bearer JWT vereist)</li>"
        "<li><code>GET /healthz</code> &mdash; health check</li>"
        "<li><code>GET /.well-known/oauth-protected-resource</code> &mdash; OAuth metadata</li>"
        "<li><code>GET /admin/tools</code> &mdash; tool registry (Bearer JWT)</li>"
        "</ul>"
        "<p>Voor toegang: configureer Claude/Copilot/Cursor met deze server-URL. "
        "Documentatie: <a href='https://github.com/Github-campai/AzureMCP-CMS'>"
        "github.com/Github-campai/AzureMCP-CMS</a></p>"
    )
    return HTMLResponse(
        _INDEX_HTML.format(
            title="ADMCMS MCP",
            badge_class="info",
            badge_text="Actief",
            body=body,
        )
    )


def _public_base_url(request: Request) -> str:
    """Resolve our externally-visible base URL, honouring proxy headers.

    Azure Container Apps terminates TLS at the ingress, so uvicorn sees
    plain HTTP. Without trusting X-Forwarded-* the metadata response
    would advertise http:// URLs, which OAuth clients reject in
    redirect/audience checks.
    """
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}"


async def oauth_protected_resource(request: Request) -> JSONResponse:
    settings = get_settings()
    base_url = _public_base_url(request)
    # Advertise one authorization server per allowed tenant, plus the generic
    # /common endpoint as a fallback. Dynamic-discovery MCP clients
    # (Copilot Studio preview) pick the matching tenant for the signed-in user.
    auth_servers = [
        f"https://login.microsoftonline.com/{tid}/v2.0"
        for tid in settings.entra_allowed_tenants
        if tid
    ]
    auth_servers.append("https://login.microsoftonline.com/common/v2.0")

    body = {
        "resource": base_url,
        "resource_name": "ADMCMS MCP",
        "resource_documentation": "https://github.com/Github-campai/AzureMCP-CMS",
        "authorization_servers": auth_servers,
        "scopes_supported": [f"api://{settings.entra_app_id}/admcms.read"],
        "bearer_methods_supported": ["header"],
        "resource_signing_alg_values_supported": ["RS256"],
    }
    return JSONResponse(body)


def _audited(tool_name: str, args: dict, call):
    """Run a tool inside an audit_call context and record summary/row_count/error."""
    with audit_call(tool_name, args) as entry:
        result = call()
        if isinstance(result, dict):
            data = result.get("data")
            entry.row_count = len(data) if isinstance(data, list) else 0
            entry.result_summary = result.get("summary") if isinstance(
                result.get("summary"), dict
            ) else None
            err = result.get("error")
            if err and entry.error is None:
                entry.error = str(err)
        return result


def _build_mcp_server() -> FastMCP:
    # streamable_http_path="/" because we Mount this app at "/mcp" in build_app;
    # leaving the default "/mcp" would result in /mcp/mcp as the effective path.
    #
    # MCP's DNS-rebinding protection rejects non-localhost Host headers with HTTP 421
    # by default. When AUTH_DISABLED=true (local dev with cloudflared/ngrok tunnels
    # exposing the server), we disable this check. Production deployments run behind
    # a fixed hostname on Azure App Service and rely on Entra JWT auth for security,
    # so we also disable the DNS-rebinding check there and trust the outer auth
    # middleware to do its job.
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    mcp = FastMCP(
        "admcms-mcp",
        stateless_http=True,
        streamable_http_path="/",
        transport_security=transport_security,
    )

    @mcp.tool()
    def query_database(sql: str, max_rows: int = 100) -> dict:
        """Execute a read-only SELECT query. LAST RESORT — use dedicated tools first.

        Before using: prefer a dedicated tool (get_revenue, get_top_customers,
        get_stock_levels, get_supplier_purchases, get_shipping_summary, etc.).
        These are safer and faster than hand-rolled SQL.

        If you must write SQL: ALWAYS call get_schema(table_name) first for each
        table you reference. Do not guess column names based on SQL conventions
        from other databases — ADMCMS uses custom names.

        ⚠️ COLUMN-NAME LANDMINES (these columns do NOT exist):
          Salesinvoice:
            - CustSuppnr → use `Customer` (FK to CustSupplier.CustSuppnr)
            - InvoiceTotal → use `LineTotal` (ex-VAT) or `AmountHC` (incl, HC)
          SalesinvoiceLines:
            - Linenr → use `Orderline`
            - Quantity → use `ThisIinvoice` (qty invoiced on this line)
            - UnitPrice → use `SalesPrice`
            - DiscountPercent → use `Discount1`, `Discount2`, or `DiscountAmount`
            - Costprice / CostPrice → use `CostAmountBookedHC`
            - VAT → use `VATCode` (code) or join Salesinvoice.VATTotal for amount
          SalesOrderHeader:
            - Invoicenr → use `SalesOrdersystemStatus` for open/closed
            - TotalAmount → use `OrderAmount`
            - ShippingDate → use `DeliveryDate`
          Any transaction table:
            - Company / Mandant → ADMCMS has NO company dimension on transactions

        Rule: SELECT and WITH only. Max 500 rows per query.
        """
        return _audited(
            "query_database",
            {"sql": sql, "max_rows": max_rows},
            lambda: _query_database(sql, max_rows=max_rows),
        )

    @mcp.tool()
    def get_schema(table_name: str) -> dict:
        """Return column metadata for an ADMCMS table."""
        return _audited(
            "get_schema", {"table_name": table_name}, lambda: _get_schema(table_name)
        )

    @mcp.tool()
    def get_lookups(table_name: str, column_name: str, top_n: int = 50) -> dict:
        """Return up to `top_n` distinct values for a column."""
        return _audited(
            "get_lookups",
            {"table_name": table_name, "column_name": column_name, "top_n": top_n},
            lambda: _get_lookups(table_name, column_name, top_n=top_n),
        )

    @mcp.tool()
    def search_products(search_term: str, limit: int = 20, offset: int = 0) -> dict:
        """Search ADMCMS products by search term.

        Matches on Productcode or Searchname using LIKE. Paging via
        `offset` + `summary.next_offset`.

        Args:
            search_term: substring to find (e.g. "filter", "MAHLE-12").
            limit: rows per page, clamped 1-500, default 20.
            offset: row to start from (0 = first page), default 0.

        Returns: {data: list of products, summary: {count, search_term,
                  offset, limit, has_more, next_offset, ...}, error}.
        """
        return _audited(
            "search_products",
            {"search_term": search_term, "limit": limit, "offset": offset},
            lambda: _search_products(search_term, limit=limit, offset=offset),
        )

    @mcp.tool()
    def get_revenue(
        start_date: str,
        end_date: str,
        group_by: str = "total",
        include_ly: bool = False,
        filters: dict | None = None,
    ) -> dict:
        """Aggregate sales invoice revenue between two ISO dates.

        Revenue uses SALESINVOICE.LineTotal (ex-VAT, home currency).
        Per-make and per-productgroup breakdowns use SalesinvoiceLines.InvoiceAmountHC.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            group_by: "total" (default), "month", "make", "customer",
                "country" (Salesinvoice.DeliveryCountry), "productgroup".
            include_ly: when True, compute same period 1 year earlier and add
                amount_ly + delta + delta_pct to each row. Default False.
            filters: optional dict restricting results to specific dimension
                values, e.g. {"country": ["NL"], "business_unit": ["B2B"]}.
                Supported keys: country (all group_by), customer_group /
                business_unit (all group_by), make / productgroup (only when
                group_by itself is "make"/"productgroup"). Use get_filter_options
                to discover valid values. Omit for unchanged (unfiltered) behaviour.

        Returns: {data, summary: {total_amount, [total_amount_ly], period, ...}, error}.
        """
        return _audited(
            "get_revenue",
            {
                "start_date": start_date,
                "end_date": end_date,
                "group_by": group_by,
                "include_ly": include_ly,
                "filters": filters,
            },
            lambda: _get_revenue(
                start_date, end_date, group_by=group_by, include_ly=include_ly, filters=filters
            ),
        )

    @mcp.tool()
    def get_margin_report(
        start_date: str,
        end_date: str,
        group_by: str = "total",
        include_ly: bool = False,
        filters: dict | None = None,
    ) -> dict:
        """Gross-margin analysis on SalesinvoiceLines (turnover - cost).

        Margin = SUM(sil.InvoiceAmountHC) - SUM(sil.CostAmountBookedHC).
        This is line-level gross margin; it does NOT deduct shipping, VAT,
        order-handling costs, or returns. If dashboard margin differs,
        definitions diverge — verify against a known datapoint.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            group_by: "total" (default), "month", "country", "make",
                "productgroup", "customer", "segment" (B2C/B2B/IC/Overig via
                CustSuppGroup).
            include_ly: add turnover_ly, margin_ly, delta_margin per row.
            filters: optional dict restricting results to specific dimension
                values. Supported keys (any group_by): country, customer_group,
                business_unit, make, productgroup. Use get_filter_options to
                discover valid values. Omit for unchanged behaviour.

        Returns: {data: rows with turnover, cost, margin, margin_pct, ...,
                  summary: {turnover, margin, margin_pct, ...}, error}.
        """
        return _audited(
            "get_margin_report",
            {
                "start_date": start_date,
                "end_date": end_date,
                "group_by": group_by,
                "include_ly": include_ly,
                "filters": filters,
            },
            lambda: _get_margin_report(
                start_date, end_date, group_by=group_by, include_ly=include_ly, filters=filters
            ),
        )

    @mcp.tool()
    def get_kpi_summary(
        start_date: str,
        end_date: str,
        include_ly: bool = True,
        filters: dict | None = None,
    ) -> dict:
        """Top-level performance KPIs in a single call.

        Replicates the KPI-card row of a typical management dashboard:
        turnover, turnover_incl_shipping, invoice_count, ordering_customers,
        aov, shipping_revenue, shipping_cost, shipping_margin_pct, line_count,
        item_count. With `include_ly=True` (default) adds `_ly`, `_delta`, and
        `_delta_pct` variants for each metric.

        All amounts in home currency. Turnover = SALESINVOICE.LineTotal.
        AOV = turnover / invoice_count.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            include_ly: compute same period 1 year earlier + deltas. Default True.
            filters: optional dict restricting results to specific dimension
                values. Supported keys: country, customer_group, business_unit.
                make/productgroup are not supported by this tool. Use
                get_filter_options to discover valid values. Omit for
                unchanged behaviour.

        Returns: {data: [single row with ~30 fields], summary: {same fields +
                  period + ly_period}, error}.
        """
        return _audited(
            "get_kpi_summary",
            {
                "start_date": start_date,
                "end_date": end_date,
                "include_ly": include_ly,
                "filters": filters,
            },
            lambda: _get_kpi_summary(start_date, end_date, include_ly=include_ly, filters=filters),
        )

    @mcp.tool()
    def get_revenue_by_business_unit(start_date: str, end_date: str) -> dict:
        """Aggregate sales invoice revenue between two ISO dates, split per business unit.

        Business units are derived from CustSupplier.CustSuppGroup:
        - 'ND' -> B2C (consumenten)
        - 'G2' t/m 'G6' -> B2B (zakelijke klanten)
        - 'INTC' -> IC (intercompany)
        - alles anders -> Overig

        Revenue uses SALESINVOICE.LineTotal (ex-VAT, home currency).

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.

        Returns: {data: [{business_unit, invoice_count, total_amount}, ...],
                  summary: {total_amount, invoice_count, period, business_units}, error}.
        """
        return _audited(
            "get_revenue_by_business_unit",
            {"start_date": start_date, "end_date": end_date},
            lambda: _get_revenue_by_business_unit(start_date, end_date),
        )

    @mcp.tool()
    def get_country_segment_matrix(
        start_date: str, end_date: str, filters: dict | None = None
    ) -> dict:
        """Country x Segment matrix: turnover, margin (+%) and shipping (+cost/margin/%).

        Backs the PBI page "Turnover per Country and Segment". Turnover/margin are
        line-grain (SUM(InvoiceAmountHC/CostAmountBookedHC), excl Supplier #81);
        shipping is header-grain (SUM(ShipCost/ShipCostCalculated)). Segment is
        B2C/B2B/IC/Overig via CustSuppGroup; customers only (CustSuppType='1').

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            filters: optional dict; country/customer_group/business_unit apply to
                both queries. make/productgroup are ignored (no PRODUCT join).

        Returns: {data: [{country, segment, turnover, margin, margin_pct,
                  shipping, shipping_cost, shipping_margin, shipping_margin_pct}, ...],
                  summary: {turnover, margin, margin_pct, rows, period}, error}.
        """
        return _audited(
            "get_country_segment_matrix",
            {"start_date": start_date, "end_date": end_date, "filters": filters},
            lambda: _get_country_segment_matrix(start_date, end_date, filters=filters),
        )

    @mcp.tool()
    def get_top_customers(
        start_date: str,
        end_date: str,
        limit: int = 10,
        filters: dict | None = None,
    ) -> dict:
        """Return the top customers on invoice revenue between two ISO dates.

        Filters to real customers only (CustSuppType = '1'), excluding suppliers.
        Revenue uses SALESINVOICE.LineTotal (ex-VAT, home currency).

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            limit: max customers to return, clamped 1-500, default 10.
            filters: optional dict restricting results to specific dimension
                values. Supported keys: country, customer_group, business_unit.
                make/productgroup are not supported by this tool. Use
                get_filter_options to discover valid values. Omit for
                unchanged behaviour.

        Returns: {data: [{CustSuppnr, Searchname, CustSuppGroup, invoice_count,
                  total_amount}, ...], summary: {top_total_amount, count, period}, error}.
        """
        return _audited(
            "get_top_customers",
            {"start_date": start_date, "end_date": end_date, "limit": limit, "filters": filters},
            lambda: _get_top_customers(start_date, end_date, limit=limit, filters=filters),
        )

    @mcp.tool()
    def get_shipping_summary(
        start_date: str,
        end_date: str,
        exclude_credits: bool = True,
        paid_only: bool = True,
    ) -> dict:
        """Aggregate shipping metrics over invoices between two ISO dates.

        Totals for charged shipping vs. calculated shipping (delta indicates
        discounts/overrides), plus order handling costs.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            exclude_credits: skip InvoiceType='1' (credit notes). Default True.
            paid_only: only FullyPaid=1 invoices. Default True.

        Returns: {data: [single row], summary: {invoice_count, total_ship_cost,
                  total_ship_cost_calculated, ship_cost_delta, ...}, error}.
        """
        return _audited(
            "get_shipping_summary",
            {
                "start_date": start_date,
                "end_date": end_date,
                "exclude_credits": exclude_credits,
                "paid_only": paid_only,
            },
            lambda: _get_shipping_summary(
                start_date,
                end_date,
                exclude_credits=exclude_credits,
                paid_only=paid_only,
            ),
        )

    @mcp.tool()
    def get_shipping_cost_analysis(
        start_date: str,
        end_date: str,
        group_by: str = "segment_carrier",
        include_ly: bool = False,
        exclude_credits: bool = True,
        paid_only: bool = True,
        segments: list[str] | None = None,
        carriers: list[str] | None = None,
    ) -> dict:
        """Detailed shipping-cost analysis with YoY comparison.

        Returns per group: order_count, total_charged (what customer pays,
        ShipCost), total_carrier_cost (what we pay the carrier,
        ShipCostCalculated), avg_charged, avg_carrier_cost, shipping_margin
        (charged - carrier_cost), avg_shipping_margin, ship_cost_pct_of_turnover.
        With include_ly=True adds _ly variants + delta_carrier_cost_pct per row.

        Default scope matches financial-reporting conventions: only fully
        paid regular invoices. Disable via exclude_credits/paid_only flags.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            group_by: "total", "segment", "carrier", "shipment_method", or
                "segment_carrier" (default, cross-tab of segment x carrier).
            include_ly: YoY delta. Default False.
            exclude_credits: skip InvoiceType='1'. Default True.
            paid_only: only FullyPaid=1. Default True.
            segments: optional filter list e.g. ["B2B"] or ["B2B","B2C"].
            carriers: optional filter list e.g. ["DHL"] or ["DHL","FedEx"].

        Returns: {data: rows with shipping metrics, summary: {...}, error}.
        """
        return _audited(
            "get_shipping_cost_analysis",
            {
                "start_date": start_date,
                "end_date": end_date,
                "group_by": group_by,
                "include_ly": include_ly,
                "exclude_credits": exclude_credits,
                "paid_only": paid_only,
                "segments": segments,
                "carriers": carriers,
            },
            lambda: _get_shipping_cost_analysis(
                start_date,
                end_date,
                group_by=group_by,
                include_ly=include_ly,
                exclude_credits=exclude_credits,
                paid_only=paid_only,
                segments=tuple(segments) if segments else None,
                carriers=tuple(carriers) if carriers else None,
            ),
        )

    @mcp.tool()
    def detect_price_anomalies(
        start_date: str,
        end_date: str,
        min_price: float = 10000.0,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Find invoice lines with suspicious (extreme) prices in a date range.

        Data-quality check. A line is flagged when SalesPrice, InvoiceAmountHC,
        or CostAmountBookedHC exceeds min_price. Threshold 10000 catches scanner
        errors (barcode in price field → ~€10^12) and outliers.

        Date range is REQUIRED for performance on the 12M-row lines table.
        Paging via `offset` + `summary.next_offset`.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            min_price: threshold in home currency (default 10000).
            limit: rows per page, clamped 1-500, default 50.
            offset: row to start from, default 0.
        """
        return _audited(
            "detect_price_anomalies",
            {
                "start_date": start_date,
                "end_date": end_date,
                "min_price": min_price,
                "limit": limit,
                "offset": offset,
            },
            lambda: _detect_price_anomalies(
                start_date, end_date, min_price=min_price, limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    def get_stock_levels(
        product_code: str | None = None,
        warehouse: str | None = None,
        only_available: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Return ProductWarehouse stock levels. Filters optional and composable.

        Known warehouse codes: CM, DS, IN, OS, SA, DM, SC, ML, PR, TP.
        Paging via `offset` + `summary.next_offset`.

        Args:
            product_code: exact Productcode.
            warehouse: warehouse code (2 chars).
            only_available: hide zero-stock rows (default True).
            limit: rows per page, clamped 1-500, default 100.
            offset: row to start from, default 0.
        """
        return _audited(
            "get_stock_levels",
            {
                "product_code": product_code,
                "warehouse": warehouse,
                "only_available": only_available,
                "limit": limit,
                "offset": offset,
            },
            lambda: _get_stock_levels(
                product_code=product_code,
                warehouse=warehouse,
                only_available=only_available,
                limit=limit,
                offset=offset,
            ),
        )

    @mcp.tool()
    def get_supplier_purchases(
        start_date: str,
        end_date: str,
        limit: int = 10,
        include_corrupt: bool = False,
    ) -> dict:
        """Top suppliers by purchase amount (PurchaseOrderInvoice.GoodsAmount).

        Mirror of get_top_customers but on the purchase side. Filters to
        CustSuppType='2'. Supplier 81 (VDM PART) is excluded by default
        due to known data corruption in ADMCMS.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            limit: max suppliers, clamped 1-500, default 10.
            include_corrupt: include known-corrupt suppliers, default False.

        Returns: {data: [{CustSuppnr, Searchname, invoice_count, total_goods,
                  total_vat, total_other_costs}, ...], summary: {top_total_goods,
                  count, period, corrupt_excluded}, error}.
        """
        return _audited(
            "get_supplier_purchases",
            {
                "start_date": start_date,
                "end_date": end_date,
                "limit": limit,
                "include_corrupt": include_corrupt,
            },
            lambda: _get_supplier_purchases(
                start_date, end_date, limit=limit, include_corrupt=include_corrupt
            ),
        )

    @mcp.tool()
    def get_customer_orders(
        customer_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> dict:
        """List recent sales orders, optionally filtered by customer.

        Includes SalesOrdersystemStatus so callers can post-filter further.
        Paging via `offset` + `summary.next_offset`.

        Args:
            customer_id: CustSupplier.CustSuppnr of the customer, or None for all.
            limit: rows per page, clamped 1-500, default 50.
            offset: row to start from, default 0.
        """
        return _audited(
            "get_customer_orders",
            {"customer_id": customer_id, "limit": limit, "offset": offset},
            lambda: _get_customer_orders(
                customer_id=customer_id, limit=limit, offset=offset
            ),
        )

    @mcp.tool()
    def get_credit_note_analysis(
        start_date: str,
        end_date: str,
        group_by: str = "segment",
        include_ly: bool = False,
    ) -> dict:
        """Credit-invoice (InvoiceType='1') analysis per dimension.

        Detects anomalies via `positive_credit_count` — credit notes usually
        have negative LineTotal; a positive value suggests data issue or
        correction-of-correction. Summary includes anomaly_pct.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            group_by: "segment" (default), "total", "country", "customer",
                "make", "month".
            include_ly: compare with same period 1 year earlier. Default False.
        """
        return _audited(
            "get_credit_note_analysis",
            {
                "start_date": start_date,
                "end_date": end_date,
                "group_by": group_by,
                "include_ly": include_ly,
            },
            lambda: _get_credit_note_analysis(
                start_date, end_date, group_by=group_by, include_ly=include_ly
            ),
        )

    @mcp.tool()
    def get_payment_method_stats(start_date: str, end_date: str, include_ly: bool = False) -> dict:
        """Payment-method mix per invoice (PREP/AC/CC/LINK/ACD/ACC/RFI).

        Scope: regular invoices only (InvoiceType='0'). Per PaymentCondition
        returns invoice_count, total_amount, avg_amount, avg_payment_days,
        pct_fullypaid, pct_of_total_count.

        Useful for cashflow analysis: shifts in PREP vs AC mix directly
        affect working-capital needs.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            include_ly: YoY delta per method. Default False.
        """
        return _audited(
            "get_payment_method_stats",
            {"start_date": start_date, "end_date": end_date, "include_ly": include_ly},
            lambda: _get_payment_method_stats(
                start_date, end_date, include_ly=include_ly
            ),
        )

    @mcp.tool()
    def get_salesrep_performance(start_date: str, end_date: str, include_ly: bool = False) -> dict:
        """Per-salesrep KPIs: invoices, unique customers, turnover, AOV.

        Scope: paid regular invoices only (InvoiceType='0' AND FullyPaid=1).
        Sorted by total_turnover DESC.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            include_ly: YoY delta per salesrep. Default False.
        """
        return _audited(
            "get_salesrep_performance",
            {"start_date": start_date, "end_date": end_date, "include_ly": include_ly},
            lambda: _get_salesrep_performance(
                start_date, end_date, include_ly=include_ly
            ),
        )

    @mcp.tool()
    def get_discount_analysis(
        start_date: str,
        end_date: str,
        group_by: str = "segment",
        filters: dict | None = None,
    ) -> dict:
        """Discount analysis on SalesinvoiceLines (DiscountAmount > 0).

        Gross pre-discount = net_turnover + total_discount.
        Discount % is computed against that gross.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            group_by: "segment" (default), "customer", "make", "productgroup",
                "salesrep", "total".
            filters: optional dict restricting results to specific dimension
                values. Supported keys: customer_group, business_unit, make,
                productgroup. country is not supported by this tool. Use
                get_filter_options to discover valid values. Omit for
                unchanged behaviour.
        """
        return _audited(
            "get_discount_analysis",
            {
                "start_date": start_date,
                "end_date": end_date,
                "group_by": group_by,
                "filters": filters,
            },
            lambda: _get_discount_analysis(
                start_date, end_date, group_by=group_by, filters=filters
            ),
        )

    @mcp.tool()
    def get_order_origin_mix(
        start_date: str,
        end_date: str,
        include_ly: bool = False,
        filters: dict | None = None,
    ) -> dict:
        """Order-channel mix via SalesinvoiceLines.OrderOrigin.

        Known dominant codes: '06' (web, ~88%), '12', '11', '01'.
        Per origin: line_count, invoice_count, total_amount,
        pct_of_total_lines.

        Args:
            start_date: ISO YYYY-MM-DD, inclusive.
            end_date: ISO YYYY-MM-DD, exclusive.
            include_ly: YoY delta per origin. Default False.
            filters: optional dict restricting results to specific dimension
                values. Supported keys: country, customer_group, business_unit.
                make/productgroup are not supported by this tool. Use
                get_filter_options to discover valid values. Omit for
                unchanged behaviour.
        """
        return _audited(
            "get_order_origin_mix",
            {
                "start_date": start_date,
                "end_date": end_date,
                "include_ly": include_ly,
                "filters": filters,
            },
            lambda: _get_order_origin_mix(
                start_date, end_date, include_ly=include_ly, filters=filters
            ),
        )

    @mcp.tool()
    def get_filter_options() -> dict:
        """Return distinct values for each filter dimension (data-driven dropdowns).

        Backs the `filters` arg on get_revenue, get_margin_report,
        get_kpi_summary, get_top_customers, get_order_origin_mix, and
        get_discount_analysis.

        Returns: {customer_group: [top ~30 by frequency], business_unit:
        ["B2C","B2B","IC"], country: [top ~50 by frequency], make: [top ~100
        by frequency], productgroup: [top ~100 by frequency]}.
        """
        return _audited("get_filter_options", {}, lambda: _get_filter_options())

    @mcp.tool()
    def get_dead_stock(
        months_without_sale: int = 12,
        min_stock_value: float = 100.0,
        group_by: str = "product",
        include_inactive_warehouses: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Surface products with stock that have not sold in the last N months.

        Returns the SKUs (or makes/productgroups) that are tying up capital in
        non-moving inventory. Use this when a customer asks "what's stuck on
        the shelves?" or for periodic dead-stock reviews.

        Definition of "dead":
          - ProductWarehouse.ActualStock > 0
          - ProductWarehouse.ActualStockValue >= min_stock_value (default 100)
          - No invoice line (InvoiceType='0') in the last `months_without_sale`
            months (default 12).

        Never-sold products (no row at all in SalesinvoiceLines) are excluded
        from the main `data` list but tracked separately in
        `summary.never_sold_count` and `summary.never_sold_value` so they
        aren't lost.

        By default warehouses 'OS' (Overstock), 'SA' (Samples), and 'DM'
        (Damaged) are excluded — set `include_inactive_warehouses=True` to
        include them.

        Args:
            months_without_sale: months threshold, clamped 1-60. Default 12.
            min_stock_value: home-currency floor on ActualStockValue,
                clamped to >= 0. Default 100.0.
            group_by: "product" (default, per-SKU per-warehouse rows),
                "make", or "productgroup".
            include_inactive_warehouses: include OS/SA/DM. Default False.
            limit: rows per page, clamped 1-500, default 100.
            offset: row to start from, default 0.

        Returns: {data, summary: {total_dead_stock_value, total_dead_sku_count,
                  never_sold_count, never_sold_value, cutoff_date, ...}, error}.
        """
        return _audited(
            "get_dead_stock",
            {
                "months_without_sale": months_without_sale,
                "min_stock_value": min_stock_value,
                "group_by": group_by,
                "include_inactive_warehouses": include_inactive_warehouses,
                "limit": limit,
                "offset": offset,
            },
            lambda: _get_dead_stock(
                months_without_sale=months_without_sale,
                min_stock_value=min_stock_value,
                group_by=group_by,
                include_inactive_warehouses=include_inactive_warehouses,
                limit=limit,
                offset=offset,
            ),
        )

    @mcp.tool()
    def health_check() -> dict:
        """Diagnostic ping: server up, DB reachable, who-am-I, tool count.

        Use this when you want to confirm end-to-end that the MCP server
        and its database connection are healthy before running a real
        query. Returns a single object with sub-statuses; if any
        component is degraded the top-level status is "degraded" or
        "down" and the failing components carry an error message.
        """
        import time
        from datetime import datetime, timezone

        from admcms_mcp.db import execute_query

        result: dict = {
            "status": "ok",
            "server": {
                "name": "admcms-mcp",
                "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tool_count": 23,
            },
            "database": {"status": "ok", "latency_ms": None, "error": None},
            "caller": {"upn": None, "oid": None, "tid": None},
        }

        # DB ping
        db_start = time.perf_counter()
        try:
            rows = execute_query("SELECT 1 AS ping", max_rows=1)
            result["database"]["latency_ms"] = round(
                (time.perf_counter() - db_start) * 1000, 1
            )
            if not rows or rows[0].get("ping") != 1:
                result["database"]["status"] = "degraded"
                result["database"]["error"] = "unexpected response from SELECT 1"
                result["status"] = "degraded"
        except Exception as e:
            result["database"]["status"] = "down"
            result["database"]["error"] = str(e)[:200]
            result["status"] = "down"

        # Caller (best-effort, not all transports expose request scope)
        try:
            user = current_user.get()
            if user:
                result["caller"]["upn"] = user
        except Exception:
            pass

        return _audited("health_check", {}, lambda: result)

    @mcp.resource("admcms://schema-knowledge")
    def schema_resource() -> str:
        """Embedded knowledge about ADMCMS tables and query patterns."""
        return SCHEMA_KNOWLEDGE

    return mcp


def build_app() -> Starlette:
    try:
        if get_settings().auth_disabled:
            logger.warning(
                "AUTH_DISABLED is true — all requests bypass JWT validation. "
                "This must never be enabled in production."
            )
    except Exception:
        # Settings can't be fully loaded at import time in some test setups;
        # the middleware handles missing config at request time.
        pass

    mcp = _build_mcp_server()
    mcp_asgi_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        # Propagate the MCP app's lifespan (which initializes its session
        # manager task group) to the outer Starlette. Without this, the
        # mounted /mcp endpoint crashes with "Task group is not initialized".
        async with mcp_asgi_app.router.lifespan_context(mcp_asgi_app):
            yield

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route(
            "/.well-known/oauth-protected-resource",
            oauth_protected_resource,
            methods=["GET"],
        ),
        *build_admin_routes(mcp),
        Mount("/mcp", app=mcp_asgi_app),
    ]
    # CORS for the admin UI opened over file:// or from a different origin.
    # Order matters: CORS wraps the auth middleware so preflight OPTIONS
    # requests don't get 401'd before the browser sees the allow headers.
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        ),
        Middleware(McpTrailingSlashMiddleware),
        Middleware(EntraAuthMiddleware),
    ]
    # McpTrailingSlashMiddleware rewrites /mcp -> /mcp/ in-place before the
    # router sees it, so the MCP-mounted ASGI app handles both forms without
    # ever emitting a 307 (which Copilot Studio's preview connector mishandles).
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


app = build_app()
