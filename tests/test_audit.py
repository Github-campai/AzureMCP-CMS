"""Tests for audit context + LocalSQLiteStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from admcms_mcp import audit
from admcms_mcp.audit_stores import LocalSQLiteStore, NullStore, build_store_from_env


@pytest.fixture
def store(tmp_path: Path) -> LocalSQLiteStore:
    return LocalSQLiteStore(path=str(tmp_path / "audit.db"))


@pytest.fixture(autouse=True)
def isolate_store(store, monkeypatch):
    """Swap the audit module's global store for each test."""
    audit.set_store(store)
    yield
    audit.set_store(None)  # force re-init for next test


def test_audit_call_records_single_entry(store):
    with audit.audit_call("test_tool", {"x": 1}) as entry:
        entry.result_summary = {"ok": True}
        entry.row_count = 3
    rows = store.query(limit=10)
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "test_tool"
    assert rows[0]["row_count"] == 3
    assert rows[0]["error"] is None


def test_audit_call_captures_exception(store):
    with pytest.raises(RuntimeError), audit.audit_call("boom", {}):
        raise RuntimeError("kaboom")
    rows = store.query(limit=10)
    assert len(rows) == 1
    assert rows[0]["error"] and "kaboom" in rows[0]["error"]


def test_record_sql_attaches_to_active_entry(store):
    with audit.audit_call("sql_tool", {}):
        audit.record_sql("SELECT 1", ("p",), 12, 5)
        audit.record_sql("SELECT 2", None, 7, 0)
    entries = store.query(limit=1)
    full = store.get(entries[0]["id"])
    assert len(full["sql_queries"]) == 2
    assert full["sql_queries"][0]["sql"] == "SELECT 1"
    assert full["sql_queries"][0]["params"] == ["p"]
    assert full["sql_queries"][0]["duration_ms"] == 12
    assert full["sql_queries"][0]["row_count"] == 5


def test_record_sql_outside_audit_is_noop(store):
    # Should not raise and should not create any entry
    audit.record_sql("SELECT orphan", None, 1, 0)
    assert store.query(limit=10) == []


def test_user_is_captured_from_context(store):
    token = audit.current_user.set("alice@campai.nl")
    try:
        with audit.audit_call("who_am_i", {}):
            pass
    finally:
        audit.current_user.reset(token)
    rows = store.query(limit=1)
    assert rows[0]["user_id"] == "alice@campai.nl"


def test_safe_args_masks_sensitive_keys(store):
    with audit.audit_call("login", {"password": "hunter2", "token": "abc", "user": "x"}):
        pass
    full = store.get(store.query(limit=1)[0]["id"])
    assert full["args"]["password"] == "***"
    assert full["args"]["token"] == "***"
    assert full["args"]["user"] == "x"


def test_store_query_filters(store):
    with audit.audit_call("tool_a", {}):
        pass
    with audit.audit_call("tool_b", {}):
        pass
    with pytest.raises(ValueError), audit.audit_call("tool_a", {}):
        raise ValueError("bad")
    assert len(store.query()) == 3
    assert [r["tool_name"] for r in store.query(tool="tool_a")] == ["tool_a", "tool_a"]
    assert len(store.query(errors_only=True)) == 1


def test_stats_per_tool_counts_errors(store):
    with audit.audit_call("t1", {}):
        pass
    with pytest.raises(RuntimeError), audit.audit_call("t1", {}):
        raise RuntimeError("x")
    stats = store.stats()
    assert stats["total_calls"] == 2
    assert stats["total_errors"] == 1
    per = {row["tool_name"]: row for row in stats["per_tool"]}
    assert per["t1"]["calls"] == 2
    assert per["t1"]["errors"] == 1


def test_null_store_accepts_everything():
    ns = NullStore()
    with audit.audit_call("noop", {}) as e:
        e.row_count = 1
    # just ensure no exception when set as global store
    audit.set_store(ns)
    with audit.audit_call("noop", {}):
        pass
    assert ns.query() == []
    assert ns.get("x") is None


def test_build_store_from_env_local(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_STORE", "local")
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "x.db"))
    s = build_store_from_env()
    assert isinstance(s, LocalSQLiteStore)


def test_build_store_from_env_none(monkeypatch):
    monkeypatch.setenv("AUDIT_STORE", "none")
    s = build_store_from_env()
    assert isinstance(s, NullStore)


def test_build_store_from_env_unknown(monkeypatch):
    monkeypatch.setenv("AUDIT_STORE", "mars")
    with pytest.raises(ValueError):
        build_store_from_env()
