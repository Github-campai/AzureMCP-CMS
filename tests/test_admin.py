"""Tests for /admin/* HTTP endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from admcms_mcp import audit
from admcms_mcp.audit_stores import LocalSQLiteStore
from admcms_mcp.server import build_app


@pytest.fixture(autouse=True)
def _auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def store(tmp_path: Path) -> LocalSQLiteStore:
    s = LocalSQLiteStore(path=str(tmp_path / "audit.db"))
    audit.set_store(s)
    yield s
    audit.set_store(None)


async def test_admin_tools_lists_registered_mcp_tools():
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] > 0
    names = {t["name"] for t in body["tools"]}
    # Spot-check a few well-known tools
    assert "query_database" in names
    assert "get_revenue" in names
    assert "get_shipping_cost_analysis" in names
    # Each tool has an input schema
    for t in body["tools"]:
        assert "input_schema" in t
        assert isinstance(t["input_schema"], dict)


async def test_admin_audit_returns_recent_entries(store):
    with audit.audit_call("demo_tool", {"x": 1}):
        audit.record_sql("SELECT 1", None, 5, 0)
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/audit?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["entries"][0]["tool_name"] == "demo_tool"


async def test_admin_audit_entry_exposes_sql(store):
    with audit.audit_call("sql_tool", {"k": "v"}):
        audit.record_sql("SELECT * FROM T", ("p",), 12, 3)
    # fetch id via query first
    entries = store.query()
    eid = entries[0]["id"]
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/admin/audit/{eid}")
    assert r.status_code == 200
    body = r.json()
    assert body["tool_name"] == "sql_tool"
    assert body["args"] == {"k": "v"}
    assert len(body["sql_queries"]) == 1
    assert body["sql_queries"][0]["sql"] == "SELECT * FROM T"
    assert body["sql_queries"][0]["params"] == ["p"]


async def test_admin_audit_entry_404_for_unknown(store):
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/audit/does-not-exist")
    assert r.status_code == 404


async def test_admin_audit_errors_only_filter(store):
    with audit.audit_call("ok_tool", {}):
        pass
    with pytest.raises(RuntimeError), audit.audit_call("bad_tool", {}):
        raise RuntimeError("boom")
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/audit?errors_only=true")
    body = r.json()
    assert body["count"] == 1
    assert body["entries"][0]["tool_name"] == "bad_tool"


async def test_admin_audit_tool_filter(store):
    with audit.audit_call("alpha", {}):
        pass
    with audit.audit_call("beta", {}):
        pass
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/audit?tool=alpha")
    body = r.json()
    assert body["count"] == 1
    assert body["entries"][0]["tool_name"] == "alpha"


async def test_admin_stats_returns_per_tool(store):
    with audit.audit_call("t_a", {}):
        pass
    with audit.audit_call("t_a", {}):
        pass
    with audit.audit_call("t_b", {}):
        pass
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] == 3
    counts = {row["tool_name"]: row["calls"] for row in body["per_tool"]}
    assert counts["t_a"] == 2
    assert counts["t_b"] == 1


async def test_admin_health_reports_store_kind(store):
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/admin/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["audit_store"]["kind"] == "LocalSQLiteStore"
    assert body["audit_store"]["ok"] is True
    assert isinstance(body["uptime_seconds"], int)
