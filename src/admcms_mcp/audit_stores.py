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
    """Production store: survives restarts and is shared across replicas.

    The SQLite store writes to container-local `/tmp`, so the trail was split
    across up to three replicas and wiped by the nightly restart. An audit log
    that disappears every night is not an audit log.

    Layout:
      PartitionKey  UTC date (YYYY-MM-DD). Bounds a time-range query to a few
                    partitions instead of scanning the table.
      RowKey        inverted tick count + entry id. Table Storage sorts only
                    ascending by RowKey, so inverting makes "newest first" the
                    natural read order rather than an in-memory sort.

    Auth is DefaultAzureCredential: the container's managed identity in
    production, the developer's `az login` locally. No key to rotate.
    """

    #: One Table Storage property caps at 64 KiB and an entity at 1 MiB. Args and
    #: SQL text can exceed that, so they are truncated with a marker rather than
    #: failing the write and losing the entry altogether.
    MAX_PROPERTY_CHARS = 30_000

    #: Ceiling for a single query, mirroring LocalSQLiteStore.
    MAX_QUERY_LIMIT = 1000

    def __init__(
        self,
        table_name: str = "admcmsaudit",
        account_url: str = "",
        connection_string: str = "",
        client: Any = None,
    ):
        self.table_name = table_name

        if client is not None:  # injected by tests
            self._client = client
            return

        from azure.data.tables import TableServiceClient

        if connection_string:
            service = TableServiceClient.from_connection_string(connection_string)
        elif account_url:
            from azure.identity import DefaultAzureCredential

            service = TableServiceClient(endpoint=account_url, credential=DefaultAzureCredential())
        else:
            raise ValueError(
                "AzureTableStore needs AUDIT_TABLE_ACCOUNT_URL "
                "(https://<account>.table.core.windows.net) or a connection string"
            )

        # Idempotent, so a fresh environment needs no manual step.
        try:
            service.create_table_if_not_exists(table_name)
        except Exception as exc:  # noqa: BLE001 - never block startup on this
            logger.warning("Could not ensure audit table %s exists: %s", table_name, exc)

        self._client = service.get_table_client(table_name)
        logger.info("AzureTableStore ready on table %s", table_name)

    def _cap(self, text: str) -> str:
        if len(text) <= self.MAX_PROPERTY_CHARS:
            return text
        keep = self.MAX_PROPERTY_CHARS - 40
        return text[:keep] + f"... [{len(text) - keep} tekens afgekapt]"

    @staticmethod
    def _partition(timestamp: str) -> str:
        # Timestamps are ISO-8601; the date prefix is enough, no parsing needed.
        return timestamp[:10] or "unknown"

    @staticmethod
    def _row_key(timestamp: str, entry_id: str) -> str:
        """Inverted timestamp, so the newest row sorts first."""
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(UTC)
        inverted = 9_999_999_999_999 - int(dt.timestamp() * 1000)
        return f"{inverted:013d}-{entry_id}"

    def log(self, entry: AuditEntry) -> None:
        sql_dicts = [asdict(q) if isinstance(q, SqlCall) else q for q in entry.sql_queries]
        self._client.upsert_entity(
            {
                "PartitionKey": self._partition(entry.timestamp),
                "RowKey": self._row_key(entry.timestamp, entry.id),
                "entry_id": entry.id,
                "timestamp": entry.timestamp,
                "tool_name": entry.tool_name,
                "args_json": self._cap(json.dumps(entry.args, default=str)),
                "sql_queries_json": self._cap(json.dumps(sql_dicts, default=str)),
                "duration_ms": int(entry.duration_ms or 0),
                "row_count": int(entry.row_count or 0),
                "user_id": entry.user or "",
                "error": self._cap(str(entry.error)) if entry.error else "",
                # A boolean rather than filtering on "error != ''": Table Storage
                # cannot filter inequality against an empty string reliably.
                "has_error": bool(entry.error),
                "result_summary_json": (
                    self._cap(json.dumps(entry.result_summary, default=str))
                    if entry.result_summary is not None
                    else ""
                ),
            }
        )

    def _filter(self, tool: str | None, since: datetime | None, errors_only: bool) -> str | None:
        clauses: list[str] = []
        if since:
            clauses.append(f"PartitionKey ge '{_iso(since)[:10]}'")
            clauses.append(f"timestamp ge '{_iso(since)}'")
        if tool:
            clauses.append("tool_name eq '{}'".format(tool.replace("'", "''")))
        if errors_only:
            clauses.append("has_error eq true")
        return " and ".join(clauses) if clauses else None

    def query(
        self,
        limit: int = 100,
        tool: str | None = None,
        since: datetime | None = None,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), self.MAX_QUERY_LIMIT))
        rows = self._client.query_entities(
            query_filter=self._filter(tool, since, errors_only),
            select=[
                "entry_id",
                "timestamp",
                "tool_name",
                "duration_ms",
                "row_count",
                "user_id",
                "error",
            ],
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": row.get("entry_id"),
                    "timestamp": row.get("timestamp"),
                    "tool_name": row.get("tool_name"),
                    "duration_ms": row.get("duration_ms"),
                    "row_count": row.get("row_count"),
                    "user_id": row.get("user_id"),
                    "error": row.get("error") or None,
                }
            )
            if len(out) >= capped:
                break
        return out

    def get(self, entry_id: str) -> dict[str, Any] | None:
        safe = entry_id.replace("'", "''")
        for row in self._client.query_entities(query_filter=f"entry_id eq '{safe}'"):
            entry: dict[str, Any] = {
                "id": row.get("entry_id"),
                "timestamp": row.get("timestamp"),
                "tool_name": row.get("tool_name"),
                "duration_ms": row.get("duration_ms"),
                "row_count": row.get("row_count"),
                "user_id": row.get("user_id"),
                "error": row.get("error") or None,
            }
            for prop, key in (
                ("args_json", "args"),
                ("sql_queries_json", "sql_queries"),
                ("result_summary_json", "result_summary"),
            ):
                raw = row.get(prop)
                try:
                    entry[key] = json.loads(raw) if raw else None
                except (TypeError, ValueError):
                    # A truncated payload is no longer valid JSON; hand back the
                    # text so the caller still sees what was captured.
                    entry[key] = raw
            return entry
        return None

    def stats(self, since: datetime | None = None) -> dict[str, Any]:
        since = since or (datetime.now(UTC) - timedelta(days=7))
        rows = self._client.query_entities(
            query_filter=self._filter(None, since, False),
            select=["tool_name", "duration_ms", "has_error"],
        )

        total = 0
        errors = 0
        per_tool: dict[str, dict[str, Any]] = {}
        for row in rows:
            total += 1
            failed = bool(row.get("has_error"))
            errors += 1 if failed else 0
            name = row.get("tool_name") or "unknown"
            bucket = per_tool.setdefault(
                name, {"tool_name": name, "calls": 0, "_ms": 0, "errors": 0}
            )
            bucket["calls"] += 1
            bucket["_ms"] += int(row.get("duration_ms") or 0)
            bucket["errors"] += 1 if failed else 0

        rollup = [
            {
                "tool_name": b["tool_name"],
                "calls": b["calls"],
                "avg_duration_ms": (b["_ms"] / b["calls"]) if b["calls"] else 0,
                "errors": b["errors"],
            }
            for b in per_tool.values()
        ]
        rollup.sort(key=lambda r: r["calls"], reverse=True)

        return {
            "since": _iso(since),
            "total_calls": total,
            "total_errors": errors,
            "error_rate_pct": (errors / total * 100) if total else 0.0,
            "per_tool": rollup,
        }


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
        return AzureTableStore(
            table_name=os.environ.get("AUDIT_TABLE_NAME", "admcmsaudit"),
            account_url=os.environ.get("AUDIT_TABLE_ACCOUNT_URL", ""),
            connection_string=os.environ.get("AUDIT_AZURE_CONNECTION_STRING", ""),
        )
    raise ValueError(f"Unknown AUDIT_STORE={mode!r}")


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
