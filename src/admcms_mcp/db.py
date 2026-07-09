"""Database connection, query validation, and execution."""

import logging
import queue
import re
import threading
from contextlib import contextmanager
from typing import Any

import pyodbc

from admcms_mcp.config import get_settings
from admcms_mcp.keyvault import get_secret

logger = logging.getLogger(__name__)

_DANGEROUS_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "EXEC",
    "EXECUTE",
    "MERGE",
    "GRANT",
    "REVOKE",
)

_DANGEROUS_PATTERN = re.compile(
    r"\b(" + "|".join(_DANGEROUS_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

import time as _time_mod

_MAX_ROWS_HARD_LIMIT = 500
# Line-grain aggregations over a 12-month range take ~60-75s on this DB; give
# them room to finish rather than erroring at the ODBC timeout (App Service
# idle timeout is ~230s, so stay well under that).
_QUERY_TIMEOUT_SECONDS = 180

# Connection POOL. The MCP runs sync tool handlers in a threadpool, so a
# dashboard's tiles arrive as concurrent requests. A single shared connection
# serialized them (one slow query starved the rest); a small pool lets them run
# in parallel, each borrowing its own connection.
_POOL_MAX = 8
_pool: "queue.Queue[pyodbc.Connection]" = queue.Queue(maxsize=_POOL_MAX)
_pool_created = 0
_pool_lock = threading.Lock()

# In-process result cache. Dashboard reads tolerate mild staleness; caching the
# heavy aggregates means only the FIRST load per (sql, params) pays the cost and
# every subsequent tile/refresh within the TTL is instant.
_QUERY_CACHE_TTL_SECONDS = 600
_query_cache: dict[tuple, tuple[float, list]] = {}
_query_cache_lock = threading.Lock()


def _cache_get(key: tuple) -> list | None:
    with _query_cache_lock:
        hit = _query_cache.get(key)
        if hit is not None and (_time_mod.monotonic() - hit[0]) < _QUERY_CACHE_TTL_SECONDS:
            return hit[1]
    return None


def _cache_put(key: tuple, result: list) -> None:
    with _query_cache_lock:
        _query_cache[key] = (_time_mod.monotonic(), result)


def validate_query(sql: str) -> None:
    """Raise ValueError unless `sql` is a pure SELECT/WITH read query."""
    stripped = sql.strip()
    if not stripped:
        raise ValueError("Query cannot be empty")

    first_token = stripped.lstrip("(").split(None, 1)[0].upper()
    if first_token not in ("SELECT", "WITH"):
        if first_token in _DANGEROUS_KEYWORDS:
            raise ValueError(f"Query contains forbidden keyword: {first_token}")
        raise ValueError(
            f"Only SELECT/WITH queries are allowed; got starting token: {first_token!r}"
        )

    match = _DANGEROUS_PATTERN.search(sql)
    if match:
        raise ValueError(f"Query contains forbidden keyword: {match.group(1).upper()}")


def _build_connection_string() -> str:
    settings = get_settings()
    password = get_secret("admcms-db-password")
    username = get_secret("admcms-db-username")
    return (
        f"DRIVER={{{settings.admcms_driver}}};"
        f"SERVER={settings.admcms_server};"
        f"DATABASE={settings.admcms_database};"
        f"UID={username};"
        f"PWD={password};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
        f"Connection Timeout=30;"
    )


def _new_connection() -> pyodbc.Connection:
    conn = pyodbc.connect(_build_connection_string(), timeout=30)
    conn.timeout = _QUERY_TIMEOUT_SECONDS
    # Read-only workload: autocommit avoids SELECTs holding an open transaction
    # (and its shared locks) between calls — a cause of query-timeout stalls.
    conn.autocommit = True
    return conn


def _acquire() -> pyodbc.Connection:
    """Check out a live connection from the pool (creating one up to _POOL_MAX,
    else blocking until one frees). Dead connections are transparently replaced."""
    global _pool_created
    conn: pyodbc.Connection | None = None
    try:
        conn = _pool.get_nowait()
    except queue.Empty:
        with _pool_lock:
            if _pool_created < _POOL_MAX:
                _pool_created += 1
                try:
                    return _new_connection()
                except Exception:
                    _pool_created -= 1
                    raise
        # Pool is at capacity and all are busy — wait for one to be returned.
        conn = _pool.get()

    try:
        conn.cursor().execute("SELECT 1").fetchone()
        return conn
    except pyodbc.Error:
        logger.warning("Pooled DB connection is dead; reconnecting.")
        try:
            conn.close()
        except Exception:
            pass
        return _new_connection()


def _release(conn: pyodbc.Connection, *, healthy: bool = True) -> None:
    """Return a connection to the pool, or discard it if the last query errored."""
    global _pool_created
    if healthy:
        try:
            _pool.put_nowait(conn)
            return
        except queue.Full:
            pass
    try:
        conn.close()
    except Exception:
        pass
    with _pool_lock:
        _pool_created -= 1


@contextmanager
def _borrow():
    """Borrow a pooled connection for one query; discard it on DB error."""
    conn = _acquire()
    healthy = True
    try:
        yield conn
    except pyodbc.Error:
        healthy = False
        raise
    finally:
        _release(conn, healthy=healthy)


def execute_query(sql: str, max_rows: int = 100) -> list[dict[str, Any]]:
    """Validate and execute a read-only SQL query; return rows as dicts."""
    import time as _time

    from admcms_mcp import audit as _audit

    validate_query(sql)
    effective_rows = min(max(1, max_rows), _MAX_ROWS_HARD_LIMIT)

    cache_key = (sql, None, effective_rows)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    _start = _time.monotonic()
    with _borrow() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(effective_rows)
    result = [dict(zip(columns, row, strict=False)) for row in rows]
    _audit.record_sql(
        sql, None, int((_time.monotonic() - _start) * 1000), len(result)
    )
    _cache_put(cache_key, result)
    return result


def execute_query_params(
    sql: str, params: tuple[Any, ...], max_rows: int = 100
) -> list[dict[str, Any]]:
    """Like execute_query but with pyodbc-style `?` parameter binding.

    Same validate_query guard, same row cap, but supports parameterized
    SQL so tool wrappers can pass user-controlled values safely.
    """
    import time as _time

    from admcms_mcp import audit as _audit

    validate_query(sql)
    effective_rows = min(max(1, max_rows), _MAX_ROWS_HARD_LIMIT)

    cache_key = (sql, params, effective_rows)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    _start = _time.monotonic()
    with _borrow() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(effective_rows)
    result = [dict(zip(columns, row, strict=False)) for row in rows]
    _audit.record_sql(
        sql, params, int((_time.monotonic() - _start) * 1000), len(result)
    )
    _cache_put(cache_key, result)
    return result


def get_schema_columns(table_name: str) -> list[dict[str, Any]]:
    """Fetch column metadata for `table_name` via a parameterized query."""
    sql = (
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = ? "
        "ORDER BY ORDINAL_POSITION"
    )
    with _borrow() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, (table_name,))
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
    return [dict(zip(columns, row, strict=False)) for row in rows]


def get_distinct_values(table_name: str, column_name: str, top_n: int = 50) -> list[Any]:
    """Fetch distinct values for a column.

    Table and column names cannot be passed as parameters in T-SQL (they
    are identifiers, not values). We therefore validate them against a
    strict identifier regex before interpolation.
    """
    if not _is_valid_identifier(table_name):
        raise ValueError(f"Invalid table name: {table_name!r}")
    if not _is_valid_identifier(column_name):
        raise ValueError(f"Invalid column name: {column_name!r}")

    effective_n = min(max(1, top_n), _MAX_ROWS_HARD_LIMIT)
    sql = (
        f"SELECT DISTINCT TOP ({effective_n}) [{column_name}] "
        f"FROM [{table_name}] "
        f"WHERE [{column_name}] IS NOT NULL"
    )
    with _borrow() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        return [row[0] for row in cursor.fetchall()]


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _is_valid_identifier(name: str) -> bool:
    """Allow only conservative T-SQL identifiers to avoid injection."""
    return bool(_IDENTIFIER_PATTERN.match(name))
