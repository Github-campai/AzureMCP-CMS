"""Audit logging for MCP tool invocations.

Captures per call: tool name, args, all SQL queries issued, duration, user,
error, and result summary. Persists via a pluggable store (local SQLite by
default; Azure Table Storage planned for production).

Design:
- `audit_call(tool_name, args)` context manager set on each tool entry point
- `record_sql(sql, params, duration_ms, row_count)` invoked by db.execute_query*
  to attach SQL to the active audit context via a ContextVar
- `current_user` ContextVar is populated by the auth middleware; audit reads it

The ContextVar approach works cleanly across async boundaries (Starlette,
MCP SDK task groups) without per-request plumbing in every tool.
"""

from __future__ import annotations

import contextvars
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ContextVar for the active audit entry (set by audit_call, read by record_sql)
_current_entry: contextvars.ContextVar[AuditEntry | None] = contextvars.ContextVar(
    "admcms_mcp_audit_entry", default=None
)

# ContextVar for current user identity (set by auth middleware)
#: Stable identity of the caller (Entra `oid`), used to key the per-user
#: concurrency limit. Separate from `current_user`, which holds the UPN because
#: that is what a human reading the audit log wants to see.
current_subject: contextvars.ContextVar[str] = contextvars.ContextVar(
    "admcms_mcp_current_subject", default="anonymous"
)

current_user: contextvars.ContextVar[str] = contextvars.ContextVar(
    "admcms_mcp_current_user", default="unknown"
)


@dataclass
class SqlCall:
    sql: str
    params: list[Any] = field(default_factory=list)
    duration_ms: int = 0
    row_count: int = 0


@dataclass
class AuditEntry:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: (
            datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
    )
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    sql_queries: list[SqlCall] = field(default_factory=list)
    duration_ms: int = 0
    row_count: int = 0
    user: str = "unknown"
    error: str | None = None
    result_summary: dict[str, Any] | None = None

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        # SqlCalls are nested dataclasses — asdict handles them
        return d


# Global store (initialised lazily by get_store so tests can swap)
_store: Any = None


def set_store(store: Any) -> None:
    global _store
    _store = store


def get_store() -> Any:
    """Lazy-init the audit store based on env config if not explicitly set."""
    global _store
    if _store is not None:
        return _store
    from admcms_mcp.audit_stores import build_store_from_env

    _store = build_store_from_env()
    return _store


@contextmanager
def audit_call(tool_name: str, args: dict[str, Any]):
    """Wrap a tool invocation to capture SQL, duration, user, errors.

    Usage:
        with audit_call("get_revenue", {"start_date": ...}) as entry:
            result = do_work()
            entry.result_summary = result.get("summary")
            entry.row_count = len(result.get("data") or [])
    """
    entry = AuditEntry(tool_name=tool_name, args=_safe_args(args), user=current_user.get())
    token = _current_entry.set(entry)
    start = time.monotonic()
    try:
        yield entry
    except Exception as e:
        entry.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        entry.duration_ms = int((time.monotonic() - start) * 1000)
        _current_entry.reset(token)
        try:
            get_store().log(entry)
        except Exception:
            # Audit logging must never break the tool call itself.
            logger.exception("Failed to write audit entry for %s", tool_name)


#: How long audit rows are kept. An audit trail exists to answer "who looked at
#: what, and when", and that question is asked long after the fact — a quarter
#: closes, a customer asks, an incident is reconstructed. A year covers the
#: reporting cycle these numbers belong to. Longer would keep personal data
#: (the UPN of every caller) with no purpose left to point at, which the AVG
#: does not allow; shorter and the trail cannot answer for the previous quarter.
DEFAULT_RETENTION_DAYS = 365


def retention_days() -> int:
    """Configured retention in days; 0 or less disables purging."""
    raw = os.environ.get("AUDIT_RETENTION_DAYS", "")
    if not raw.strip():
        return DEFAULT_RETENTION_DAYS
    try:
        return int(raw)
    except ValueError:
        logger.warning("AUDIT_RETENTION_DAYS=%r is not a number; keeping the default", raw)
        return DEFAULT_RETENTION_DAYS


def purge_expired() -> int:
    """Drop audit rows past the retention age. Returns how many were removed.

    Blocking: the stores are synchronous. Callers on the event loop must hand
    this to a worker thread.
    """
    days = retention_days()
    if days <= 0:
        logger.info("Audit retention disabled (AUDIT_RETENTION_DAYS=%s)", days)
        return 0
    try:
        return get_store().purge_older_than(days)
    except Exception:
        # Housekeeping must never take the server down with it.
        logger.exception("Audit purge failed")
        return 0


def current_entry() -> AuditEntry | None:
    """The audit entry for the tool call in progress, or None outside one.

    `errors.client_error` uses this to attach the full exception text to the
    call being audited while handing the client only a reference.
    """
    return _current_entry.get()


def record_sql(sql: str, params: tuple | list | None, duration_ms: int, row_count: int) -> None:
    """Register an executed SQL call against the active audit entry.

    No-op if there is no active audit context (e.g. direct CLI/script use).
    """
    entry = _current_entry.get()
    if entry is None:
        return
    entry.sql_queries.append(
        SqlCall(
            sql=sql,
            params=list(params) if params else [],
            duration_ms=duration_ms,
            row_count=row_count,
        )
    )


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """Convert args to JSON-friendly types; hide obviously-sensitive keys."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k.lower() in {"password", "api_key", "token", "secret"}:
            out[k] = "***"
            continue
        try:
            # Ensure value is JSON-serialisable; fall back to repr.
            import json as _json

            _json.dumps(v, default=str)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = repr(v)
    return out
