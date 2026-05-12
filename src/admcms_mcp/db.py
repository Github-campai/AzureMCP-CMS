"""Database connection, query validation, and execution."""

import logging
import re
import threading
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

_MAX_ROWS_HARD_LIMIT = 500
_QUERY_TIMEOUT_SECONDS = 60

_connection: pyodbc.Connection | None = None
_connection_lock = threading.Lock()


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


def _get_connection() -> pyodbc.Connection:
    """Return a shared pyodbc connection, reconnecting if dead."""
    global _connection
    with _connection_lock:
        if _connection is not None:
            try:
                _connection.cursor().execute("SELECT 1").fetchone()
                return _connection
            except pyodbc.Error:
                logger.warning("Existing DB connection is dead; reconnecting.")
                _connection = None

        _connection = pyodbc.connect(_build_connection_string(), timeout=30)
        _connection.timeout = _QUERY_TIMEOUT_SECONDS
        return _connection


def execute_query(sql: str, max_rows: int = 100) -> list[dict[str, Any]]:
    """Validate and execute a read-only SQL query; return rows as dicts."""
    import time as _time

    from admcms_mcp import audit as _audit

    validate_query(sql)
    effective_rows = min(max(1, max_rows), _MAX_ROWS_HARD_LIMIT)

    conn = _get_connection()
    cursor = conn.cursor()
    _start = _time.monotonic()
    cursor.execute(sql)
    columns = [col[0] for col in cursor.description] if cursor.description else []
    rows = cursor.fetchmany(effective_rows)
    result = [dict(zip(columns, row, strict=False)) for row in rows]
    _audit.record_sql(
        sql, None, int((_time.monotonic() - _start) * 1000), len(result)
    )
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

    conn = _get_connection()
    cursor = conn.cursor()
    _start = _time.monotonic()
    cursor.execute(sql, params)
    columns = [col[0] for col in cursor.description] if cursor.description else []
    rows = cursor.fetchmany(effective_rows)
    result = [dict(zip(columns, row, strict=False)) for row in rows]
    _audit.record_sql(
        sql, params, int((_time.monotonic() - _start) * 1000), len(result)
    )
    return result


def get_schema_columns(table_name: str) -> list[dict[str, Any]]:
    """Fetch column metadata for `table_name` via a parameterized query."""
    conn = _get_connection()
    cursor = conn.cursor()
    sql = (
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = ? "
        "ORDER BY ORDINAL_POSITION"
    )
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
    conn = _get_connection()
    cursor = conn.cursor()
    sql = (
        f"SELECT DISTINCT TOP ({effective_n}) [{column_name}] "
        f"FROM [{table_name}] "
        f"WHERE [{column_name}] IS NOT NULL"
    )
    cursor.execute(sql)
    return [row[0] for row in cursor.fetchall()]


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _is_valid_identifier(name: str) -> bool:
    """Allow only conservative T-SQL identifiers to avoid injection."""
    return bool(_IDENTIFIER_PATTERN.match(name))
