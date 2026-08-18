"""Pluggable persistence backends for the audit log.

LocalSQLiteStore — stdlib-only, file-based, perfect for local dev and
single-instance VM deployments. Path defaults to `data/audit.db` but is
configurable via the AUDIT_DB_PATH env var.

AzureTableStore — stub for now. Once the VM deployment pattern is agreed,
implement with `azure-data-tables` SDK. Switching store is one env-var
change; all calling code uses the Store protocol.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from admcms_mcp.audit import AuditEntry, SqlCall

logger = logging.getLogger(__name__)


class AuditStore(Protocol):
    def log(self, entry: AuditEntry) -> None: ...
    def query(
        self,
        limit: int = 100,
        tool: str | None = None,
        since: datetime | None = None,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]: ...
    def get(self, entry_id: str) -> dict[str, Any] | None: ...
    def stats(self, since: datetime | None = None) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Local SQLite implementation
# ---------------------------------------------------------------------------


class LocalSQLiteStore:
    """File-based SQLite store. Thread-safe via a single lock + per-call
    connection (sqlite3 is fast enough for audit-log volume on a single VM)."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        args_json TEXT NOT NULL,
        sql_queries_json TEXT NOT NULL,
        duration_ms INTEGER NOT NULL,
        row_count INTEGER DEFAULT 0,
        user_id TEXT,
        error TEXT,
        result_summary_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit_log(tool_name);
    CREATE INDEX IF NOT EXISTS idx_audit_errors ON audit_log(error) WHERE error IS NOT NULL;
    """

    def __init__(self, path: str = "data/audit.db"):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(p)
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(self.SCHEMA)
        logger.info("LocalSQLiteStore ready at %s", self.path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def log(self, entry: AuditEntry) -> None:
        sql_queries_dicts = [asdict(q) if isinstance(q, SqlCall) else q for q in entry.sql_queries]
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO audit_log "
                "(id, timestamp, tool_name, args_json, sql_queries_json, "
                "duration_ms, row_count, user_id, error, result_summary_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    entry.id,
                    entry.timestamp,
                    entry.tool_name,
                    json.dumps(entry.args, default=str),
                    json.dumps(sql_queries_dicts, default=str),
                    entry.duration_ms,
                    entry.row_count,
                    entry.user,
                    entry.error,
                    json.dumps(entry.result_summary, default=str)
                    if entry.result_summary is not None
                    else None,
                ),
            )

    def query(
        self,
        limit: int = 100,
        tool: str | None = None,
        since: datetime | None = None,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if tool:
            where.append("tool_name = ?")
            params.append(tool)
        if since:
            where.append("timestamp >= ?")
            params.append(_iso(since))
        if errors_only:
            where.append("error IS NOT NULL")
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        sql = (
            "SELECT id, timestamp, tool_name, duration_ms, row_count, "
            "user_id, error "
            f"FROM audit_log{where_sql} "
            "ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(max(1, min(int(limit), 1000)))
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get(self, entry_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        # Expand JSON columns for the API consumer
        for k in ("args_json", "sql_queries_json", "result_summary_json"):
            raw = d.pop(k, None)
            key = k.replace("_json", "")
            d[key] = json.loads(raw) if raw else None
        return d

    def stats(self, since: datetime | None = None) -> dict[str, Any]:
        since = since or (datetime.now(UTC) - timedelta(days=7))
        since_iso = _iso(since)
        with self._lock, self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM audit_log WHERE timestamp >= ?",
                (since_iso,),
            ).fetchone()["c"]
            errors = conn.execute(
                "SELECT COUNT(*) AS c FROM audit_log WHERE timestamp >= ? AND error IS NOT NULL",
                (since_iso,),
            ).fetchone()["c"]
            per_tool = conn.execute(
                "SELECT tool_name, COUNT(*) AS calls, "
                "AVG(duration_ms) AS avg_duration_ms, "
                "SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors "
                "FROM audit_log WHERE timestamp >= ? "
                "GROUP BY tool_name ORDER BY calls DESC",
                (since_iso,),
            ).fetchall()
        return {
            "since": since_iso,
            "total_calls": total,
            "total_errors": errors,
            "error_rate_pct": (errors / total * 100) if total else 0.0,
            "per_tool": [dict(r) for r in per_tool],
        }


# ---------------------------------------------------------------------------
# Azure Table Storage (stub)
# ---------------------------------------------------------------------------


class AzureTableStore:
    """Stub — implement once the VM deployment uses a shared Azure Storage account.

    The intent is to keep identical semantics to LocalSQLiteStore, so this
    class will receive `log`, `query`, `get`, `stats` with the same signatures.
    """

    def __init__(self, connection_string: str, table_name: str = "admcmsaudit"):
        raise NotImplementedError(
            "AzureTableStore is a stub. Implement with azure-data-tables "
            "when migrating to shared Azure Storage."
        )


# ---------------------------------------------------------------------------
# Null store (for tests / audit-disabled mode)
# ---------------------------------------------------------------------------


class NullStore:
    """Discard all audit calls. Used when AUDIT_STORE=none (tests / e2e)."""

    def log(self, entry: AuditEntry) -> None:
        pass

    def query(self, **kwargs) -> list[dict[str, Any]]:
        return []

    def get(self, entry_id: str) -> dict[str, Any] | None:
        return None

    def stats(self, since: datetime | None = None) -> dict[str, Any]:
        return {
            "since": _iso(since or datetime.now(UTC)),
            "total_calls": 0,
            "total_errors": 0,
            "error_rate_pct": 0.0,
            "per_tool": [],
        }


def build_store_from_env() -> AuditStore:
    mode = os.environ.get("AUDIT_STORE", "local").lower()
    if mode == "local":
        path = os.environ.get("AUDIT_DB_PATH", "data/audit.db")
        return LocalSQLiteStore(path)
    if mode == "none":
        return NullStore()
    if mode == "azure_table":
        conn = os.environ.get("AUDIT_AZURE_CONNECTION_STRING", "")
        return AzureTableStore(conn)
    raise ValueError(f"Unknown AUDIT_STORE={mode!r}")


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
