"""Client-facing errors carry a reference, never the driver's own words."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pyodbc
import pytest
from httpx import ASGITransport, AsyncClient

from admcms_mcp import audit
from admcms_mcp.audit_stores import LocalSQLiteStore
from admcms_mcp.errors import client_error
from admcms_mcp.server import build_app

#: What pyodbc actually says when a login fails: driver, host, database.
PYODBC_LEAK = (
    "('28000', \"[28000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
    "Login failed for user 'admcms_mcp'. (18456) (SQLDriverConnect); "
    '[28000] Cannot open database \\"ADMCMS-PRODUCTION-REPL01\\" requested by the login. '
    'Server: cmsnl.database.windows.net")'
)

LEAKY_FRAGMENTS = [
    "cmsnl.database.windows.net",
    "ODBC Driver 18",
    "ADMCMS-PRODUCTION-REPL01",
    "admcms_mcp",
]


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


class TestMasking:
    def test_message_carries_no_driver_host_or_database(self):
        message = client_error(pyodbc.Error(PYODBC_LEAK))

        for fragment in LEAKY_FRAGMENTS:
            assert fragment not in message, f"{fragment!r} leaked to the client"

    def test_message_carries_a_reference(self):
        message = client_error(RuntimeError("boom"))

        assert "reference" in message.lower()

    def test_reference_is_the_audit_entry_id(self):
        with audit.audit_call("get_revenue", {}) as entry:
            message = client_error(RuntimeError("boom"))

        assert entry.id in message

    def test_full_text_lands_on_the_audit_entry(self):
        with audit.audit_call("get_revenue", {}) as entry:
            client_error(pyodbc.Error(PYODBC_LEAK))

        assert "cmsnl.database.windows.net" in entry.error

    def test_outside_an_audit_context_it_still_produces_a_reference(self):
        message = client_error(RuntimeError("boom"))

        assert "reference" in message.lower()
        assert len(message) < 300

    def test_action_opens_the_message(self):
        assert client_error(RuntimeError("boom"), action="Database ping").startswith(
            "Database ping failed"
        )


class TestEndToEnd:
    """The reference a tool hands out must resolve under /admin/audit/{id}."""

    @patch("admcms_mcp.tools.execute_query_params")
    async def test_reference_from_a_failing_tool_is_retrievable(self, mock_execute, store):
        from admcms_mcp.server import _audited
        from admcms_mcp.tools import search_products

        mock_execute.side_effect = pyodbc.Error(PYODBC_LEAK)
        result = _audited(
            "search_products", {"search_term": "filter"}, lambda: search_products("filter")
        )

        client_message = result["error"]
        for fragment in LEAKY_FRAGMENTS:
            assert fragment not in client_message

        reference = client_message.split("reference ")[1].split(")")[0]

        app = build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            response = await c.get(f"/admin/audit/{reference}")

        assert response.status_code == 200, "the reference must resolve for an administrator"
        entry = response.json()
        assert entry["tool_name"] == "search_products"
        assert "cmsnl.database.windows.net" in entry["error"], (
            "the full text must survive where the administrator can read it"
        )
