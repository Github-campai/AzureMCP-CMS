"""Admin HTTP endpoints: tools overview, audit log, stats, health.

Backed by the audit store selected via env (`AUDIT_STORE`). All admin
routes inherit the same EntraAuthMiddleware as the rest of the app; in
AUTH_DISABLED mode (local dev) they are open.

Endpoints:
    GET /admin/tools                 registered MCP tools + their schemas
    GET /admin/audit                 recent audit rows (filters: limit,
                                     tool, since, errors_only)
    GET /admin/audit/{id}            full audit entry incl. SQL queries
    GET /admin/stats                 aggregated per-tool statistics
    GET /admin/health                audit-store health + uptime

The admin UI (web/admin.html) consumes these endpoints.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from admcms_mcp.audit import get_store

logger = logging.getLogger(__name__)

_START_TIME = time.monotonic()


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _truthy(raw: str | None) -> bool:
    return str(raw or "").lower() in ("1", "true", "yes", "on")


def build_admin_routes(mcp: FastMCP) -> list[Route]:
    """Return Starlette routes for /admin/* endpoints.

    Takes the FastMCP instance so /admin/tools can enumerate registered
    tools without reaching into private state.
    """

    async def tools_endpoint(request: Request) -> JSONResponse:
        tools = await mcp.list_tools()
        data = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in tools
        ]
        return JSONResponse({"count": len(data), "tools": data})

    async def audit_endpoint(request: Request) -> JSONResponse:
        q = request.query_params
        try:
            limit = int(q.get("limit", "100"))
        except ValueError:
            limit = 100
        tool = q.get("tool") or None
        since = _parse_since(q.get("since"))
        errors_only = _truthy(q.get("errors_only"))
        rows = await asyncio.to_thread(
            get_store().query,
            limit=limit,
            tool=tool,
            since=since,
            errors_only=errors_only,
        )
        return JSONResponse({"count": len(rows), "entries": rows})

    async def audit_entry_endpoint(request: Request) -> JSONResponse:
        entry_id = request.path_params["entry_id"]
        row = await asyncio.to_thread(get_store().get, entry_id)
        if row is None:
            return JSONResponse({"error": "not_found", "id": entry_id}, status_code=404)
        return JSONResponse(row)

    async def stats_endpoint(request: Request) -> JSONResponse:
        since = _parse_since(request.query_params.get("since"))
        stats = await asyncio.to_thread(get_store().stats, since)
        return JSONResponse(stats)

    async def health_endpoint(request: Request) -> JSONResponse:
        store = get_store()
        store_kind = type(store).__name__
        store_path: Any = getattr(store, "path", None)
        # Store reachability: a tiny stats() call is cheap and exercises the
        # backing store once per hit.
        try:
            stats = await asyncio.to_thread(store.stats, None)
            store_ok = True
            total_calls = stats.get("total_calls", 0)
        except Exception as e:  # noqa: BLE001 — surface any backend failure
            logger.warning("Audit store unhealthy: %s", e)
            store_ok = False
            total_calls = None

        return JSONResponse(
            {
                "status": "ok" if store_ok else "degraded",
                "uptime_seconds": int(time.monotonic() - _START_TIME),
                "audit_store": {
                    "kind": store_kind,
                    "path": store_path,
                    "ok": store_ok,
                    "total_calls_last_7d": total_calls,
                },
            }
        )

    return [
        Route("/admin/tools", tools_endpoint, methods=["GET"]),
        Route("/admin/audit", audit_endpoint, methods=["GET"]),
        Route("/admin/audit/{entry_id}", audit_entry_endpoint, methods=["GET"]),
        Route("/admin/stats", stats_endpoint, methods=["GET"]),
        Route("/admin/health", health_endpoint, methods=["GET"]),
    ]
