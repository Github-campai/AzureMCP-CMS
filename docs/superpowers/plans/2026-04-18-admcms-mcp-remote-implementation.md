# ADMCMS MCP Remote Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the local stdio ADMCMS MCP server into a remote-capable HTTP MCP server on Azure App Service with Entra ID SSO, while keeping all existing query tools functional.

**Architecture:** FastMCP with Streamable HTTP transport, wrapped in Starlette with JWT validation middleware. OAuth 2.1 Resource Server pattern, Entra ID multi-tenant app registration. Hybrid allowlist (Entra security group + Key Vault JSON). Deployed via Bicep to Azure App Service. Secrets in Key Vault, accessed via System-Assigned Managed Identity. CI/CD via GitHub Actions.

**Tech Stack:** Python 3.12, `mcp[cli]>=1.2`, `authlib`, `pyodbc`, `pydantic-settings`, `azure-identity`, `azure-keyvault-secrets`, `starlette`, `uvicorn`, `pytest`, Bicep, Azure App Service (Linux container), Azure Key Vault, Microsoft Entra ID.

**Design spec:** [`docs/superpowers/specs/2026-04-18-admcms-mcp-remote-design.md`](../specs/2026-04-18-admcms-mcp-remote-design.md)

---

## Phase Overview

- **Phase 1 (Tasks 1–7):** Local refactor — split `server.py` into modules, add tests, SELECT-only guard, SQL injection fix. No deployment yet.
- **Phase 2 (Tasks 8–14):** HTTP server + auth — MCP Streamable HTTP transport, JWT validation, hybrid allowlist, OAuth metadata endpoint. Locally testable.
- **Phase 3 (Tasks 15–18):** Azure infrastructure — Bicep for App Service + Key Vault + RBAC; manual Entra app registration; first deploy to dev.
- **Phase 4 (Tasks 19–21):** CI/CD — Dockerfile, GitHub Actions for CI + deploy.
- **Phase 5 (Tasks 22–25):** Production deployment + end-to-end validation + documentation finalization.

**Tasks marked 🛠 require manual user action** (Azure Portal, Entra admin center, GitHub org settings). Non-🛠 tasks can run unattended by an implementation agent.

---

## Pre-flight

- [ ] **Verify current working directory**

Run: `pwd` (should end in `cms-mcp`)

- [ ] **Verify current git state is clean except for untracked legacy files**

Run: `git status --short`
Expected output contains: `?? server.py`, `?? test_connection.py`, `?? start_server.bat`, `?? requirements.txt`, `?? handleiding_admcms_mcp.md`, `?? files.zip`, `?? DATABASE-KENNIS/`. HEAD is on `main` branch, ahead of origin or no remote.

- [ ] **Delete files.zip (approved in design)**

Run: `rm files.zip`

---

## Phase 1 — Local Refactor & Unit Tests

### Task 1: Project scaffolding — pyproject.toml, .gitignore, .env.example

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/admcms_mcp/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create pyproject.toml**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "admcms-mcp"
version = "0.1.0"
description = "ADMCMS remote MCP server with Entra SSO"
requires-python = ">=3.12"
dependencies = [
    "mcp[cli]>=1.2.0",
    "pyodbc>=5.1.0",
    "authlib>=1.3.0",
    "starlette>=0.37.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.6.0",
    "pydantic-settings>=2.2.0",
    "azure-identity>=1.16.0",
    "azure-keyvault-secrets>=4.8.0",
    "httpx>=0.27.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
    "pytest-mock>=3.14.0",
    "ruff>=0.4.0",
    "pip-audit>=2.7.0",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "SIM"]
ignore = ["E501"]  # line length handled by formatter

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-v --strict-markers"

[tool.coverage.run]
source = ["src/admcms_mcp"]
omit = ["*/tests/*"]
```

- [ ] **Step 2: Create .gitignore**

Create `.gitignore`:

```
# Python
__pycache__/
*.pyc
*.pyo
*.pyd
*.egg-info/
build/
dist/

# Virtual environments
.venv/
venv/
env/

# Environment / secrets
.env
.env.local
.env.*.local

# Test & coverage
.pytest_cache/
.coverage
htmlcov/
coverage.xml

# Linting
.ruff_cache/
.mypy_cache/

# IDEs
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db

# Legacy (to be removed)
files.zip

# Azure
.azure/
*.publishsettings
```

- [ ] **Step 3: Create .env.example**

Create `.env.example`:

```
# Azure Key Vault URI (production reads secrets from here)
KEY_VAULT_URI=https://kv-admcms-mcp-dev.vault.azure.net/

# Entra App Registration — application (client) ID
ENTRA_APP_ID=00000000-0000-0000-0000-000000000000

# Comma-separated list of Entra tenant IDs allowed to authenticate
ENTRA_ALLOWED_TENANTS=campai-tenant-id,cms-lelystad-tenant-id

# Entra security group ID for Campai users (optional; leave blank if using only Key Vault allowlist)
CAMPAI_ALLOWED_GROUP=00000000-0000-0000-0000-000000000000

# Local dev only — disable auth entirely for tool-logic debugging (NEVER true in production)
AUTH_DISABLED=false

# Database (optional override for local dev; production reads from Key Vault)
ADMCMS_SERVER=cmsnl.database.windows.net
ADMCMS_DATABASE=ADMCMS-PRODUCTION-REPL01
ADMCMS_DRIVER=ODBC Driver 18 for SQL Server
```

- [ ] **Step 4: Create package markers**

Create `src/admcms_mcp/__init__.py`:

```python
"""ADMCMS remote MCP server."""

__version__ = "0.1.0"
```

Create `tests/__init__.py` (empty file).

- [ ] **Step 5: Install in editable mode**

Run:
```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash; use .venv/bin/activate on Linux/Mac
pip install -e ".[dev]"
```

Expected: All dependencies install without errors. `pip list` shows `admcms-mcp 0.1.0 (editable)`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore .env.example src/admcms_mcp/__init__.py tests/__init__.py
git commit -m "chore: scaffold Python project with deps and linting"
```

---

### Task 2: Extract SCHEMA_KNOWLEDGE to schema.py

**Files:**
- Create: `src/admcms_mcp/schema.py`
- Reference: existing `server.py` (SCHEMA_KNOWLEDGE string constant)

- [ ] **Step 1: Read existing SCHEMA_KNOWLEDGE from server.py**

Open `server.py`, locate the `SCHEMA_KNOWLEDGE` constant (large multi-line string documenting 45 tables). Copy its content.

- [ ] **Step 2: Create schema.py with the extracted content**

Create `src/admcms_mcp/schema.py`:

```python
"""Static knowledge about the ADMCMS database schema.

This string is embedded into MCP resources so Claude can reason about
tables and query patterns without hitting the DB repeatedly.
"""

SCHEMA_KNOWLEDGE = """
<<<COPY THE ENTIRE SCHEMA_KNOWLEDGE STRING FROM THE EXISTING server.py HERE, PRESERVING FORMATTING>>>
"""
```

Replace the placeholder with the exact content of `SCHEMA_KNOWLEDGE` from `server.py`, preserving triple-quoted string formatting.

- [ ] **Step 3: Write a smoke test**

Create `tests/test_schema.py`:

```python
from admcms_mcp.schema import SCHEMA_KNOWLEDGE


def test_schema_knowledge_is_non_empty():
    assert isinstance(SCHEMA_KNOWLEDGE, str)
    assert len(SCHEMA_KNOWLEDGE) > 1000


def test_schema_knowledge_mentions_core_tables():
    # Sanity check: key tables should be documented
    for table in ["ARTIKEL", "ORDER", "RELATIE"]:
        assert table in SCHEMA_KNOWLEDGE.upper()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_schema.py -v`
Expected: Both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/admcms_mcp/schema.py tests/test_schema.py
git commit -m "refactor: extract SCHEMA_KNOWLEDGE to dedicated module"
```

---

### Task 3: Config module with Pydantic Settings

**Files:**
- Create: `src/admcms_mcp/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing test for config loading from env**

Create `tests/test_config.py`:

```python
import pytest
from admcms_mcp.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv-test.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "app-id-123")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "tenant-a,tenant-b")
    monkeypatch.setenv("ADMCMS_SERVER", "test.db")

    s = Settings()

    assert s.key_vault_uri == "https://kv-test.vault.azure.net/"
    assert s.entra_app_id == "app-id-123"
    assert s.entra_allowed_tenants == ["tenant-a", "tenant-b"]
    assert s.admcms_server == "test.db"
    assert s.auth_disabled is False


def test_settings_auth_disabled_from_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "id")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "t")
    monkeypatch.setenv("AUTH_DISABLED", "true")

    s = Settings()
    assert s.auth_disabled is True


def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("KEY_VAULT_URI", raising=False)
    monkeypatch.delenv("ENTRA_APP_ID", raising=False)
    monkeypatch.delenv("ENTRA_ALLOWED_TENANTS", raising=False)

    with pytest.raises(Exception):  # Pydantic ValidationError
        Settings()
```

- [ ] **Step 2: Run test — expect failure**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'admcms_mcp.config'`.

- [ ] **Step 3: Implement config.py**

Create `src/admcms_mcp/config.py`:

```python
"""Typed settings loaded from environment variables.

In production, secrets referenced here (DB password, allowlist JSON) are
loaded from Azure Key Vault at runtime via `keyvault.py`, not from env.
Only non-secret config lives here.
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Azure Key Vault — where secrets come from in production
    key_vault_uri: str

    # Entra app registration identifiers
    entra_app_id: str
    entra_allowed_tenants: list[str]
    campai_allowed_group: str = ""  # optional; empty disables group check

    # Database connection (non-secret parts; password comes from Key Vault)
    admcms_server: str = "cmsnl.database.windows.net"
    admcms_database: str = "ADMCMS-PRODUCTION-REPL01"
    admcms_driver: str = "ODBC Driver 18 for SQL Server"

    # Escape hatches (development only)
    auth_disabled: bool = False

    @field_validator("entra_allowed_tenants", mode="before")
    @classmethod
    def split_tenants(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_config.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/admcms_mcp/config.py tests/test_config.py
git commit -m "feat(config): typed settings via pydantic-settings"
```

---

### Task 4: Query validator — SELECT-only guard (TDD)

**Files:**
- Create: `src/admcms_mcp/db.py` (validator part only; connection comes in Task 5)
- Create: `tests/test_db_validator.py`

- [ ] **Step 1: Write failing tests for query validator**

Create `tests/test_db_validator.py`:

```python
import pytest
from admcms_mcp.db import validate_query


class TestValidateQuery:
    def test_accepts_simple_select(self):
        validate_query("SELECT * FROM foo")

    def test_accepts_select_lowercase(self):
        validate_query("select * from foo")

    def test_accepts_select_with_whitespace(self):
        validate_query("   \n  SELECT 1   ")

    def test_accepts_cte(self):
        validate_query("WITH c AS (SELECT 1) SELECT * FROM c")

    def test_accepts_select_with_where(self):
        validate_query("SELECT id FROM ARTIKEL WHERE ARTNR = 'ABC'")

    def test_rejects_empty_query(self):
        with pytest.raises(ValueError, match="empty"):
            validate_query("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(ValueError, match="empty"):
            validate_query("   \n\t  ")

    @pytest.mark.parametrize(
        "keyword",
        [
            "INSERT INTO foo VALUES (1)",
            "UPDATE foo SET bar = 1",
            "DELETE FROM foo",
            "DROP TABLE foo",
            "ALTER TABLE foo ADD COLUMN bar INT",
            "CREATE TABLE foo (id INT)",
            "TRUNCATE TABLE foo",
            "EXEC sp_stuff",
            "EXECUTE sp_stuff",
            "MERGE foo USING bar",
            "GRANT SELECT ON foo TO bar",
            "REVOKE SELECT ON foo FROM bar",
        ],
    )
    def test_rejects_non_select_statements(self, keyword):
        with pytest.raises(ValueError):
            validate_query(keyword)

    def test_rejects_select_followed_by_dml(self):
        # SQL injection pattern: multi-statement with DML after SELECT
        with pytest.raises(ValueError, match="INSERT"):
            validate_query("SELECT 1; INSERT INTO foo VALUES (1)")

    def test_rejects_dangerous_keyword_in_comment_stripped(self):
        # Guard against keyword in block comment being missed or hit
        # Current implementation: we don't strip comments, so keyword
        # inside comment WILL be caught. This is conservative-safe.
        with pytest.raises(ValueError):
            validate_query("SELECT 1 /* INSERT */")

    def test_dangerous_keyword_substring_does_not_match(self):
        # "INSERTED" is not INSERT; a column named "DROPDOWN" is not DROP
        validate_query("SELECT * FROM foo WHERE col = 'INSERTED_VALUE'")
        validate_query("SELECT DROPDOWN FROM foo")
```

- [ ] **Step 2: Run tests — expect failure**

Run: `pytest tests/test_db_validator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'admcms_mcp.db'`.

- [ ] **Step 3: Implement validator in db.py**

Create `src/admcms_mcp/db.py`:

```python
"""Database connection and query execution.

Currently contains only the SELECT-only query validator; connection
and execution logic are added in a later task.
"""

import re

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


def validate_query(sql: str) -> None:
    """Raise ValueError unless `sql` is a pure SELECT/WITH read query.

    Rules:
      - Must be non-empty after stripping whitespace.
      - First token (ignoring leading `(`) must be SELECT or WITH.
      - Must not contain any DML/DDL keyword as a whole word, even later
        in the string. This also catches multi-statement injections.

    We intentionally also block keywords inside comments. A read-only
    query has no reason to contain those words, so false positives are
    acceptable trade-off for defense in depth.
    """
    stripped = sql.strip()
    if not stripped:
        raise ValueError("Query cannot be empty")

    # Detect first statement keyword, allowing leading ( for subquery-style
    first_token = stripped.lstrip("(").split(None, 1)[0].upper()
    if first_token not in ("SELECT", "WITH"):
        # If the first keyword is itself dangerous, surface that specifically
        if first_token in _DANGEROUS_KEYWORDS:
            raise ValueError(f"Query contains forbidden keyword: {first_token}")
        raise ValueError(
            f"Only SELECT/WITH queries are allowed; got starting token: {first_token!r}"
        )

    match = _DANGEROUS_PATTERN.search(sql)
    if match:
        raise ValueError(f"Query contains forbidden keyword: {match.group(1).upper()}")
```

- [ ] **Step 4: Run tests — expect all pass**

Run: `pytest tests/test_db_validator.py -v`
Expected: All 17 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/admcms_mcp/db.py tests/test_db_validator.py
git commit -m "feat(db): SELECT-only query validator with comprehensive guards"
```

---

### Task 5: DB connection layer + query execution

**Files:**
- Modify: `src/admcms_mcp/db.py`
- Create: `tests/test_db_connection.py`

- [ ] **Step 1: Write failing test for execute_query with mocked pyodbc**

Create `tests/test_db_connection.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from admcms_mcp.db import execute_query, get_schema_columns


class TestExecuteQuery:
    @patch("admcms_mcp.db._get_connection")
    def test_execute_query_returns_rows_as_dicts(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.description = [("id", None), ("name", None)]
        mock_cursor.fetchmany.return_value = [(1, "foo"), (2, "bar")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        rows = execute_query("SELECT id, name FROM foo", max_rows=10)

        assert rows == [{"id": 1, "name": "foo"}, {"id": 2, "name": "bar"}]
        mock_cursor.execute.assert_called_once_with("SELECT id, name FROM foo")
        mock_cursor.fetchmany.assert_called_once_with(10)

    @patch("admcms_mcp.db._get_connection")
    def test_execute_query_rejects_dangerous_query(self, mock_get_conn):
        with pytest.raises(ValueError, match="INSERT"):
            execute_query("INSERT INTO foo VALUES (1)")
        mock_get_conn.assert_not_called()

    @patch("admcms_mcp.db._get_connection")
    def test_execute_query_caps_max_rows_at_500(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.description = [("id", None)]
        mock_cursor.fetchmany.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        execute_query("SELECT id FROM foo", max_rows=99999)

        mock_cursor.fetchmany.assert_called_once_with(500)


class TestGetSchemaColumns:
    @patch("admcms_mcp.db._get_connection")
    def test_get_schema_uses_parameterized_query(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("COLUMN_NAME", None),
            ("DATA_TYPE", None),
            ("CHARACTER_MAXIMUM_LENGTH", None),
            ("IS_NULLABLE", None),
        ]
        mock_cursor.fetchall.return_value = [("id", "int", None, "NO")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = get_schema_columns("ARTIKEL")

        # Parameterized query: SQL should not contain the table name as literal
        called_sql, called_params = mock_cursor.execute.call_args[0]
        assert "?" in called_sql
        assert called_params == ("ARTIKEL",)
        assert result == [
            {
                "COLUMN_NAME": "id",
                "DATA_TYPE": "int",
                "CHARACTER_MAXIMUM_LENGTH": None,
                "IS_NULLABLE": "NO",
            }
        ]

    @patch("admcms_mcp.db._get_connection")
    def test_get_schema_injection_attempt_does_not_inject(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.description = [("COLUMN_NAME", None)]
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        # Attacker-controlled table name with injection attempt
        get_schema_columns("foo'; DROP TABLE bar--")

        called_sql, called_params = mock_cursor.execute.call_args[0]
        # The malicious string is a parameter value, not SQL
        assert called_params == ("foo'; DROP TABLE bar--",)
        assert "DROP" not in called_sql.upper()
```

- [ ] **Step 2: Run test — expect failure**

Run: `pytest tests/test_db_connection.py -v`
Expected: FAIL with `ImportError: cannot import name 'execute_query' from 'admcms_mcp.db'`.

- [ ] **Step 3: Extend db.py with connection + execution**

Replace the contents of `src/admcms_mcp/db.py` with:

```python
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
    validate_query(sql)
    effective_rows = min(max(1, max_rows), _MAX_ROWS_HARD_LIMIT)

    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(sql)
    columns = [col[0] for col in cursor.description] if cursor.description else []
    rows = cursor.fetchmany(effective_rows)
    return [dict(zip(columns, row, strict=False)) for row in rows]


def get_schema_columns(table_name: str) -> list[dict[str, Any]]:
    """Fetch column metadata for `table_name` via a parameterized query.

    Uses `?` placeholder to prevent SQL injection on the table name input.
    """
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
    are identifiers, not values). We therefore validate them against
    INFORMATION_SCHEMA before interpolation.
    """
    if not _is_valid_identifier(table_name):
        raise ValueError(f"Invalid table name: {table_name!r}")
    if not _is_valid_identifier(column_name):
        raise ValueError(f"Invalid column name: {column_name!r}")

    effective_n = min(max(1, top_n), _MAX_ROWS_HARD_LIMIT)
    conn = _get_connection()
    cursor = conn.cursor()
    # Identifiers are validated above; interpolation is safe here.
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
```

Note: `keyvault.py` is implemented in the next step. The test for `execute_query` and `get_schema_columns` mocks `_get_connection`, so it will work before `keyvault` is wired up, as long as `admcms_mcp.keyvault` is importable.

- [ ] **Step 4: Create keyvault.py stub**

Create `src/admcms_mcp/keyvault.py`:

```python
"""Azure Key Vault secret loader.

In production, secrets are fetched via Managed Identity.
In local dev, DefaultAzureCredential falls back to the developer's
`az login` credentials.
"""

import logging
from functools import lru_cache

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from admcms_mcp.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _client() -> SecretClient:
    settings = get_settings()
    credential = DefaultAzureCredential()
    return SecretClient(vault_url=settings.key_vault_uri, credential=credential)


@lru_cache(maxsize=32)
def get_secret(name: str) -> str:
    """Fetch a secret by name. Cached for the process lifetime."""
    client = _client()
    secret = client.get_secret(name)
    logger.debug("Fetched secret %s from Key Vault", name)
    return secret.value or ""
```

- [ ] **Step 5: Run tests — expect all pass**

Run: `pytest tests/test_db_connection.py tests/test_db_validator.py -v`
Expected: All tests PASS (existing validator tests still pass; new connection tests pass).

- [ ] **Step 6: Commit**

```bash
git add src/admcms_mcp/db.py src/admcms_mcp/keyvault.py tests/test_db_connection.py
git commit -m "feat(db): connection pool, parameterized schema query, Key Vault secret loader"
```

---

### Task 6: MCP tool handlers (tools.py)

**Files:**
- Create: `src/admcms_mcp/tools.py`
- Create: `tests/test_tools.py`

- [ ] **Step 1: Write failing tests for tool wrappers**

Create `tests/test_tools.py`:

```python
from unittest.mock import patch

import pytest

from admcms_mcp.tools import get_lookups, get_schema, query_database


class TestQueryDatabase:
    @patch("admcms_mcp.tools.execute_query")
    def test_returns_structured_result(self, mock_execute):
        mock_execute.return_value = [{"id": 1}, {"id": 2}]

        result = query_database("SELECT id FROM foo", max_rows=10)

        assert result["rows"] == [{"id": 1}, {"id": 2}]
        assert result["row_count"] == 2
        assert "sql" in result
        mock_execute.assert_called_once_with("SELECT id FROM foo", max_rows=10)

    @patch("admcms_mcp.tools.execute_query")
    def test_returns_error_for_invalid_sql(self, mock_execute):
        mock_execute.side_effect = ValueError("Query contains forbidden keyword: INSERT")

        result = query_database("INSERT INTO foo VALUES (1)")

        assert "error" in result
        assert "INSERT" in result["error"]
        assert result.get("rows") is None or result.get("rows") == []


class TestGetSchema:
    @patch("admcms_mcp.tools.get_schema_columns")
    def test_returns_column_info(self, mock_get_cols):
        mock_get_cols.return_value = [
            {"COLUMN_NAME": "id", "DATA_TYPE": "int", "IS_NULLABLE": "NO"},
        ]

        result = get_schema("ARTIKEL")

        assert result["table"] == "ARTIKEL"
        assert result["columns"] == [
            {"COLUMN_NAME": "id", "DATA_TYPE": "int", "IS_NULLABLE": "NO"},
        ]
        mock_get_cols.assert_called_once_with("ARTIKEL")

    @patch("admcms_mcp.tools.get_schema_columns")
    def test_returns_error_on_unknown_table(self, mock_get_cols):
        mock_get_cols.return_value = []

        result = get_schema("DOES_NOT_EXIST")

        assert "error" in result or result["columns"] == []


class TestGetLookups:
    @patch("admcms_mcp.tools.get_distinct_values")
    def test_returns_distinct_values(self, mock_get_distinct):
        mock_get_distinct.return_value = ["A", "B", "C"]

        result = get_lookups("ARTIKEL", "MERK", top_n=10)

        assert result["table"] == "ARTIKEL"
        assert result["column"] == "MERK"
        assert result["values"] == ["A", "B", "C"]
        mock_get_distinct.assert_called_once_with("ARTIKEL", "MERK", top_n=10)

    @patch("admcms_mcp.tools.get_distinct_values")
    def test_returns_error_on_invalid_identifier(self, mock_get_distinct):
        mock_get_distinct.side_effect = ValueError("Invalid table name")

        result = get_lookups("foo'; DROP--", "bar")

        assert "error" in result
        assert "Invalid" in result["error"]
```

- [ ] **Step 2: Run tests — expect failure**

Run: `pytest tests/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'admcms_mcp.tools'`.

- [ ] **Step 3: Implement tools.py**

Create `src/admcms_mcp/tools.py`:

```python
"""MCP tool handlers.

Pure functions: each takes primitive inputs, returns a JSON-serializable
dict. Error responses use {"error": "..."} so MCP clients (Claude) can
relay the problem to the user without the call failing at protocol level.
"""

import datetime
import logging
from typing import Any

from admcms_mcp.db import execute_query, get_distinct_values, get_schema_columns

logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    """Convert DB values that aren't JSON-serializable (dates, decimals, etc.)."""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if hasattr(value, "__float__") and not isinstance(value, (int, float, bool)):
        return float(value)
    return value


def _convert_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _jsonable(v) for k, v in row.items()} for row in rows]


def query_database(sql: str, max_rows: int = 100) -> dict[str, Any]:
    """Execute a read-only SQL query against ADMCMS and return rows."""
    try:
        rows = execute_query(sql, max_rows=max_rows)
        return {
            "sql": sql,
            "row_count": len(rows),
            "rows": _convert_rows(rows),
        }
    except ValueError as e:
        logger.info("Query rejected: %s", e)
        return {"sql": sql, "error": str(e), "rows": []}
    except Exception as e:
        logger.exception("Query execution failed")
        return {"sql": sql, "error": f"Execution error: {e}", "rows": []}


def get_schema(table_name: str) -> dict[str, Any]:
    """Return column metadata for an ADMCMS table."""
    try:
        columns = get_schema_columns(table_name)
        if not columns:
            return {"table": table_name, "error": "Table not found or no columns", "columns": []}
        return {"table": table_name, "columns": columns}
    except ValueError as e:
        return {"table": table_name, "error": str(e), "columns": []}
    except Exception as e:
        logger.exception("get_schema failed")
        return {"table": table_name, "error": f"Execution error: {e}", "columns": []}


def get_lookups(table_name: str, column_name: str, top_n: int = 50) -> dict[str, Any]:
    """Return up to `top_n` distinct values for a column in a table."""
    try:
        values = get_distinct_values(table_name, column_name, top_n=top_n)
        return {
            "table": table_name,
            "column": column_name,
            "values": [_jsonable(v) for v in values],
        }
    except ValueError as e:
        return {"table": table_name, "column": column_name, "error": str(e), "values": []}
    except Exception as e:
        logger.exception("get_lookups failed")
        return {
            "table": table_name,
            "column": column_name,
            "error": f"Execution error: {e}",
            "values": [],
        }
```

- [ ] **Step 4: Run tests — expect all pass**

Run: `pytest tests/test_tools.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/admcms_mcp/tools.py tests/test_tools.py
git commit -m "feat(tools): MCP tool handlers for query, schema, lookups"
```

---

### Task 7: Full suite green, Phase 1 closure

- [ ] **Step 1: Run full test suite**

Run: `pytest -v --cov=admcms_mcp`
Expected: All tests PASS. Coverage for `db.py` and `tools.py` should be >80%.

- [ ] **Step 2: Run linter**

Run: `ruff check src tests`
Expected: No issues. If issues: `ruff check --fix src tests` then re-run.

- [ ] **Step 3: Run formatter check**

Run: `ruff format --check src tests`
Expected: "X files already formatted". If not: `ruff format src tests` then commit formatting fixes separately.

- [ ] **Step 4: Commit any lint/format fixes (if needed)**

```bash
git add src tests
git commit -m "style: apply ruff formatting"
```

---

## Phase 2 — HTTP Server + Auth

### Task 8: Minimal Starlette HTTP server with /healthz (no MCP/auth yet)

**Files:**
- Create: `src/admcms_mcp/server.py`
- Create: `tests/test_server_healthz.py`

- [ ] **Step 1: Write failing test for /healthz**

Create `tests/test_server_healthz.py`:

```python
from httpx import ASGITransport, AsyncClient

from admcms_mcp.server import build_app


async def test_healthz_returns_200_and_status():
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_server_healthz.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError: cannot import name 'build_app'`.

- [ ] **Step 3: Implement minimal server.py**

Create `src/admcms_mcp/server.py`:

```python
"""HTTP server entry point: Starlette app hosting the MCP Streamable HTTP endpoint.

Layered in later tasks:
  - /mcp endpoint (FastMCP Streamable HTTP transport)
  - JWT validation middleware
  - /.well-known/oauth-protected-resource metadata
"""

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


async def healthz(request):
    return JSONResponse({"status": "ok"})


def build_app() -> Starlette:
    routes = [
        Route("/healthz", healthz, methods=["GET"]),
    ]
    return Starlette(routes=routes)


app = build_app()
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_server_healthz.py -v`
Expected: PASS.

- [ ] **Step 5: Smoke test locally**

Run: `uvicorn admcms_mcp.server:app --port 8000 &`
Then: `curl http://localhost:8000/healthz`
Expected: `{"status":"ok"}`
Stop the server: `kill %1`

- [ ] **Step 6: Commit**

```bash
git add src/admcms_mcp/server.py tests/test_server_healthz.py
git commit -m "feat(server): minimal Starlette app with /healthz"
```

---

### Task 9: Mount MCP Streamable HTTP transport at /mcp

**Files:**
- Modify: `src/admcms_mcp/server.py`
- Create: `tests/test_server_mcp.py`

- [ ] **Step 1: Write test that /mcp endpoint is reachable**

Create `tests/test_server_mcp.py`:

```python
from httpx import ASGITransport, AsyncClient

from admcms_mcp.server import build_app


async def test_mcp_endpoint_exists():
    """A POST to /mcp with an empty body should not 404.

    We don't test the full MCP protocol here — that's covered by MCP SDK's
    own tests. We just verify the route is mounted.
    """
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp", json={}, headers={"Accept": "application/json, text/event-stream"}
        )
    # Expect 400/406/422 (bad request) rather than 404 (not found)
    assert response.status_code != 404
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_server_mcp.py -v`
Expected: FAIL (404) or assertion error.

- [ ] **Step 3: Add MCP server with tools mounted at /mcp**

Replace `src/admcms_mcp/server.py`:

```python
"""HTTP server entry point: Starlette app hosting the MCP Streamable HTTP endpoint."""

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from admcms_mcp.schema import SCHEMA_KNOWLEDGE
from admcms_mcp.tools import get_lookups as _get_lookups
from admcms_mcp.tools import get_schema as _get_schema
from admcms_mcp.tools import query_database as _query_database


def _build_mcp_server() -> FastMCP:
    mcp = FastMCP("admcms-mcp", stateless_http=True)

    @mcp.tool()
    def query_database(sql: str, max_rows: int = 100) -> dict:
        """Execute a read-only SELECT query against the ADMCMS database."""
        return _query_database(sql, max_rows=max_rows)

    @mcp.tool()
    def get_schema(table_name: str) -> dict:
        """Return column metadata for an ADMCMS table."""
        return _get_schema(table_name)

    @mcp.tool()
    def get_lookups(table_name: str, column_name: str, top_n: int = 50) -> dict:
        """Return up to `top_n` distinct values for a column."""
        return _get_lookups(table_name, column_name, top_n=top_n)

    @mcp.resource("admcms://schema-knowledge")
    def schema_resource() -> str:
        """Embedded knowledge about ADMCMS tables and query patterns."""
        return SCHEMA_KNOWLEDGE

    return mcp


async def healthz(request):
    return JSONResponse({"status": "ok"})


def build_app() -> Starlette:
    mcp = _build_mcp_server()
    mcp_asgi_app = mcp.streamable_http_app()

    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        Mount("/mcp", app=mcp_asgi_app),
    ]
    return Starlette(routes=routes)


app = build_app()
```

- [ ] **Step 4: Run test — expect pass**

Run: `pytest tests/test_server_mcp.py tests/test_server_healthz.py -v`
Expected: Both PASS.

Note: If `FastMCP.streamable_http_app()` does not exist in the installed `mcp` SDK version, check the installed version (`pip show mcp`). Upgrade: `pip install --upgrade "mcp[cli]>=1.2.0"`. If the API is different (e.g., `mcp.streamable_http_app` vs `mcp.get_asgi_app()`), adjust per the SDK docs. Update `pyproject.toml` with the confirmed working version.

- [ ] **Step 5: Commit**

```bash
git add src/admcms_mcp/server.py tests/test_server_mcp.py
git commit -m "feat(server): mount MCP Streamable HTTP transport at /mcp with 3 tools"
```

---

### Task 10: JWT validator (TDD with synthetic tokens)

**Files:**
- Create: `src/admcms_mcp/auth.py`
- Create: `tests/test_auth_jwt.py`
- Create: `tests/conftest.py` (shared JWT fixtures)

- [ ] **Step 1: Add test helpers for generating synthetic JWTs**

Create `tests/conftest.py`:

```python
import time

import pytest
from authlib.jose import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_key():
    """A test RSA key-pair used to sign synthetic JWTs."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {"private_pem": private_pem, "public_pem": public_pem, "kid": "test-kid-1"}


@pytest.fixture
def make_token(rsa_key):
    def _make(
        *,
        iss: str = "https://login.microsoftonline.com/campai-tenant-id/v2.0",
        aud: str = "api://admcms-mcp",
        oid: str = "user-oid-1",
        tid: str = "campai-tenant-id",
        upn: str = "roland@campai.nl",
        groups: list[str] | None = None,
        scp: str = "admcms.read",
        exp_offset: int = 3600,
    ) -> str:
        payload = {
            "iss": iss,
            "aud": aud,
            "oid": oid,
            "tid": tid,
            "upn": upn,
            "scp": scp,
            "exp": int(time.time()) + exp_offset,
            "iat": int(time.time()),
        }
        if groups is not None:
            payload["groups"] = groups
        header = {"alg": "RS256", "kid": rsa_key["kid"]}
        token = jwt.encode(header, payload, rsa_key["private_pem"])
        return token.decode() if isinstance(token, bytes) else token

    return _make
```

Install test-only deps:

```bash
pip install cryptography
```

Add to `pyproject.toml` `[project.optional-dependencies].dev`: `"cryptography>=42.0"`.

- [ ] **Step 2: Add test dep to pyproject.toml**

Edit `pyproject.toml`, add `"cryptography>=42.0"` to the `dev` list. Then re-install: `pip install -e ".[dev]"`.

- [ ] **Step 3: Write failing JWT validation tests**

Create `tests/test_auth_jwt.py`:

```python
from unittest.mock import patch

import pytest

from admcms_mcp.auth import InvalidToken, validate_jwt


@pytest.fixture
def jwks_patch(rsa_key):
    """Patch auth.get_jwks to return our test public key."""
    fake_jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": rsa_key["kid"],
                "use": "sig",
                "alg": "RS256",
                # We patch _fetch_jwks_raw to just return the PEM bytes;
                # auth.validate_jwt will read the pem as the key.
            }
        ]
    }
    with patch("admcms_mcp.auth._get_key_for_kid") as mock:
        mock.return_value = rsa_key["public_pem"]
        yield mock


@pytest.fixture
def settings_patch(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id,cms-lelystad-tenant-id")
    # Clear cached settings
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()


class TestValidateJwt:
    def test_valid_token_returns_claims(self, make_token, jwks_patch, settings_patch):
        token = make_token()
        claims = validate_jwt(token)
        assert claims["oid"] == "user-oid-1"
        assert claims["tid"] == "campai-tenant-id"

    def test_expired_token_rejected(self, make_token, jwks_patch, settings_patch):
        token = make_token(exp_offset=-60)
        with pytest.raises(InvalidToken, match="expired"):
            validate_jwt(token)

    def test_wrong_audience_rejected(self, make_token, jwks_patch, settings_patch):
        token = make_token(aud="api://some-other-app")
        with pytest.raises(InvalidToken, match="audience"):
            validate_jwt(token)

    def test_untrusted_tenant_rejected(self, make_token, jwks_patch, settings_patch):
        token = make_token(
            iss="https://login.microsoftonline.com/hostile-tenant/v2.0",
            tid="hostile-tenant",
        )
        with pytest.raises(InvalidToken, match="tenant|issuer"):
            validate_jwt(token)

    def test_missing_scope_rejected(self, make_token, jwks_patch, settings_patch):
        token = make_token(scp="wrong.scope")
        with pytest.raises(InvalidToken, match="scope"):
            validate_jwt(token)

    def test_malformed_token_rejected(self, jwks_patch, settings_patch):
        with pytest.raises(InvalidToken):
            validate_jwt("not.a.jwt")
```

- [ ] **Step 4: Run tests — expect failure**

Run: `pytest tests/test_auth_jwt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'admcms_mcp.auth'`.

- [ ] **Step 5: Implement auth.py JWT validation**

Create `src/admcms_mcp/auth.py`:

```python
"""JWT validation for Entra ID bearer tokens + hybrid allowlist check.

Implements the OAuth 2.1 Resource Server pattern from MCP spec 2025-03-26.
"""

import json
import logging
import time
from functools import lru_cache
from typing import Any

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError

from admcms_mcp.config import get_settings
from admcms_mcp.keyvault import get_secret

logger = logging.getLogger(__name__)


class InvalidToken(Exception):
    """Raised when a JWT fails validation."""


class Forbidden(Exception):
    """Raised when a valid token does not meet allowlist requirements."""


_JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 24 * 3600


def _fetch_jwks_raw() -> list[dict[str, Any]]:
    now = time.time()
    cached = _JWKS_CACHE.get("keys")
    if cached and now - _JWKS_CACHE["fetched_at"] < _JWKS_TTL_SECONDS:
        return cached
    response = httpx.get(_JWKS_URL, timeout=10)
    response.raise_for_status()
    keys = response.json().get("keys", [])
    _JWKS_CACHE["keys"] = keys
    _JWKS_CACHE["fetched_at"] = now
    return keys


def _get_key_for_kid(kid: str) -> Any:
    for jwk in _fetch_jwks_raw():
        if jwk.get("kid") == kid:
            return JsonWebKey.import_key(jwk)
    # Force refresh once on miss, in case of key rotation
    _JWKS_CACHE["fetched_at"] = 0
    for jwk in _fetch_jwks_raw():
        if jwk.get("kid") == kid:
            return JsonWebKey.import_key(jwk)
    raise InvalidToken(f"Unknown signing key: kid={kid}")


def validate_jwt(token: str) -> dict[str, Any]:
    """Validate signature, expiration, audience, issuer, scope. Return claims."""
    settings = get_settings()

    try:
        unverified = JsonWebToken(["RS256"]).decode(
            token,
            key=lambda header, payload: _get_key_for_kid(header["kid"]),
        )
    except JoseError as e:
        raise InvalidToken(f"Invalid token signature: {e}") from e
    except Exception as e:  # malformed
        raise InvalidToken(f"Malformed token: {e}") from e

    claims = dict(unverified)

    # exp
    exp = claims.get("exp")
    if not exp or exp < time.time():
        raise InvalidToken("Token is expired")

    # aud
    expected_aud = settings.entra_app_id
    aud = claims.get("aud")
    if aud != expected_aud and expected_aud not in (aud if isinstance(aud, list) else [aud]):
        raise InvalidToken(f"Wrong audience: got {aud}, expected {expected_aud}")

    # iss (must match one of the allowed tenants)
    iss = claims.get("iss", "")
    tid = claims.get("tid", "")
    if tid not in settings.entra_allowed_tenants:
        raise InvalidToken(f"Untrusted tenant: {tid}")
    expected_iss_prefix = f"https://login.microsoftonline.com/{tid}"
    if not iss.startswith(expected_iss_prefix):
        raise InvalidToken(f"Issuer does not match tenant: {iss}")

    # scp
    scp = claims.get("scp", "")
    scopes = scp.split() if isinstance(scp, str) else scp
    if "admcms.read" not in scopes:
        raise InvalidToken(f"Missing required scope admcms.read; got: {scopes}")

    return claims


_ALLOWLIST_CACHE: dict[str, Any] = {"data": None, "fetched_at": 0.0}
_ALLOWLIST_TTL_SECONDS = 300


def _load_allowlist() -> dict[str, list[str]]:
    now = time.time()
    cached = _ALLOWLIST_CACHE.get("data")
    if cached and now - _ALLOWLIST_CACHE["fetched_at"] < _ALLOWLIST_TTL_SECONDS:
        return cached
    raw = get_secret("admcms-allowlist")
    data = json.loads(raw) if raw else {"users": [], "groups": []}
    data.setdefault("users", [])
    data.setdefault("groups", [])
    _ALLOWLIST_CACHE["data"] = data
    _ALLOWLIST_CACHE["fetched_at"] = now
    return data


def check_access(claims: dict[str, Any]) -> None:
    """Raise Forbidden unless the claim subject is on the allowlist.

    Order: (1) Campai group membership, (2) Key Vault JSON allowlist.
    Tenant check already happened in validate_jwt.
    """
    settings = get_settings()
    oid = claims.get("oid", "")
    groups = claims.get("groups") or []
    upn = claims.get("upn", "")

    # Campai group allowlist (Entra security group in Campai tenant)
    if settings.campai_allowed_group and settings.campai_allowed_group in groups:
        logger.info("Access granted via Campai group: upn=%s oid=%s", upn, oid)
        return

    # Key Vault allowlist
    try:
        allowlist = _load_allowlist()
    except Exception as e:
        logger.exception("Failed to load allowlist from Key Vault")
        raise Forbidden(f"Allowlist unavailable: {e}") from e

    if oid in allowlist["users"]:
        logger.info("Access granted via user allowlist: upn=%s oid=%s", upn, oid)
        return

    if set(groups) & set(allowlist["groups"]):
        logger.info("Access granted via group allowlist: upn=%s oid=%s", upn, oid)
        return

    logger.info("Access denied: upn=%s oid=%s tid=%s", upn, oid, claims.get("tid"))
    raise Forbidden("User not on allowlist")
```

- [ ] **Step 6: Run tests — expect all pass**

Run: `pytest tests/test_auth_jwt.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/admcms_mcp/auth.py tests/test_auth_jwt.py tests/conftest.py pyproject.toml
git commit -m "feat(auth): JWT validation with JWKS caching and Entra tenant filter"
```

---

### Task 11: Allowlist check tests

**Files:**
- Create: `tests/test_auth_allowlist.py`

- [ ] **Step 1: Write allowlist tests**

Create `tests/test_auth_allowlist.py`:

```python
import json
from unittest.mock import patch

import pytest

from admcms_mcp.auth import Forbidden, _ALLOWLIST_CACHE, check_access


@pytest.fixture(autouse=True)
def clear_allowlist_cache():
    _ALLOWLIST_CACHE["data"] = None
    _ALLOWLIST_CACHE["fetched_at"] = 0.0
    yield


@pytest.fixture
def settings_with_group(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    monkeypatch.setenv("CAMPAI_ALLOWED_GROUP", "campai-group-1")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()


class TestCheckAccess:
    def test_grants_via_campai_group(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": [], "groups": []})
            check_access(
                {
                    "oid": "some-oid",
                    "upn": "user@campai.nl",
                    "groups": ["campai-group-1", "other-group"],
                    "tid": "campai-tenant-id",
                }
            )

    def test_grants_via_user_allowlist(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": ["cms-user-oid"], "groups": []})
            check_access(
                {
                    "oid": "cms-user-oid",
                    "upn": "user@cmslelystad.nl",
                    "groups": [],
                    "tid": "cms-tenant",
                }
            )

    def test_grants_via_group_allowlist(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": [], "groups": ["kv-group-1"]})
            check_access(
                {
                    "oid": "some-oid",
                    "upn": "user@cmslelystad.nl",
                    "groups": ["kv-group-1"],
                    "tid": "cms-tenant",
                }
            )

    def test_denies_when_not_on_any_list(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = json.dumps({"users": [], "groups": []})
            with pytest.raises(Forbidden, match="allowlist"):
                check_access(
                    {
                        "oid": "stranger",
                        "upn": "stranger@example.com",
                        "groups": ["unrelated-group"],
                        "tid": "campai-tenant-id",
                    }
                )

    def test_empty_allowlist_and_no_campai_group_denies(self, settings_with_group):
        with patch("admcms_mcp.auth.get_secret") as mock_kv:
            mock_kv.return_value = ""  # no secret yet
            with pytest.raises(Forbidden):
                check_access(
                    {
                        "oid": "user",
                        "upn": "user@campai.nl",
                        "groups": [],
                        "tid": "campai-tenant-id",
                    }
                )
```

- [ ] **Step 2: Run tests — expect all pass**

Run: `pytest tests/test_auth_allowlist.py -v`
Expected: All 5 tests PASS. If some fail, debug by inspecting which branch of `check_access` fires.

- [ ] **Step 3: Commit**

```bash
git add tests/test_auth_allowlist.py
git commit -m "test(auth): hybrid allowlist coverage (Campai group + Key Vault JSON)"
```

---

### Task 12: Auth middleware + OAuth metadata endpoint + /mcp protection

**Files:**
- Modify: `src/admcms_mcp/server.py`
- Create: `tests/test_server_auth.py`

- [ ] **Step 1: Write failing tests for auth middleware behavior**

Create `tests/test_server_auth.py`:

```python
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from admcms_mcp.server import build_app


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    monkeypatch.setenv("AUTH_DISABLED", "false")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()


async def test_mcp_without_token_returns_401_with_www_authenticate():
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        )
    assert response.status_code == 401
    assert "Bearer" in response.headers.get("www-authenticate", "")
    assert "resource_metadata" in response.headers.get("www-authenticate", "")


async def test_oauth_protected_resource_metadata_published():
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    body = response.json()
    assert "resource" in body
    assert "authorization_servers" in body
    assert "scopes_supported" in body
    assert "admcms.read" in body["scopes_supported"]


async def test_healthz_is_public():
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200


async def test_mcp_with_valid_token_and_allowlist_passes():
    claims = {"oid": "user", "upn": "u@campai.nl", "groups": [], "tid": "campai-tenant-id"}
    with (
        patch("admcms_mcp.server.validate_jwt", return_value=claims),
        patch("admcms_mcp.server.check_access", return_value=None),
    ):
        app = build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={
                    "Authorization": "Bearer fake-token",
                    "Accept": "application/json, text/event-stream",
                },
            )
    # Should NOT be 401 (auth passed); may be 200/400/422 depending on MCP handling
    assert response.status_code != 401


async def test_mcp_with_valid_token_but_not_on_allowlist_returns_403():
    from admcms_mcp.auth import Forbidden

    claims = {"oid": "stranger", "upn": "x@example.com", "groups": [], "tid": "campai-tenant-id"}
    with (
        patch("admcms_mcp.server.validate_jwt", return_value=claims),
        patch("admcms_mcp.server.check_access", side_effect=Forbidden("not allowed")),
    ):
        app = build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={"Authorization": "Bearer fake-token"},
            )
    assert response.status_code == 403


async def test_auth_disabled_bypasses_check(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()

    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code != 401
```

- [ ] **Step 2: Run tests — expect failure**

Run: `pytest tests/test_server_auth.py -v`
Expected: FAIL (no middleware or OAuth metadata endpoint yet).

- [ ] **Step 3: Add auth middleware and metadata endpoint to server.py**

Replace `src/admcms_mcp/server.py`:

```python
"""HTTP server: Starlette app with MCP Streamable HTTP + Entra JWT auth."""

import logging

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from admcms_mcp.auth import Forbidden, InvalidToken, check_access, validate_jwt
from admcms_mcp.config import get_settings
from admcms_mcp.schema import SCHEMA_KNOWLEDGE
from admcms_mcp.tools import get_lookups as _get_lookups
from admcms_mcp.tools import get_schema as _get_schema
from admcms_mcp.tools import query_database as _query_database

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = frozenset({"/healthz", "/.well-known/oauth-protected-resource"})


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

        settings = get_settings()
        if settings.auth_disabled:
            request.state.user_claims = {"oid": "local-dev", "upn": "dev@localhost"}
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return self._unauthorized(request, "Missing bearer token")

        token = auth_header[7:].strip()
        try:
            claims = validate_jwt(token)
        except InvalidToken as e:
            return self._unauthorized(request, str(e))

        try:
            check_access(claims)
        except Forbidden as e:
            return JSONResponse(
                {"error": "forbidden", "detail": str(e)},
                status_code=403,
            )

        request.state.user_claims = claims
        return await call_next(request)

    def _unauthorized(self, request: Request, detail: str) -> Response:
        resource_metadata_url = (
            f"{request.url.scheme}://{request.url.netloc}/.well-known/oauth-protected-resource"
        )
        www_auth = (
            f'Bearer realm="admcms-mcp", '
            f'error="invalid_token", '
            f'error_description="{detail}", '
            f'resource_metadata="{resource_metadata_url}"'
        )
        return JSONResponse(
            {"error": "unauthorized", "detail": detail},
            status_code=401,
            headers={"WWW-Authenticate": www_auth},
        )


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def oauth_protected_resource(request: Request) -> JSONResponse:
    base_url = f"{request.url.scheme}://{request.url.netloc}"
    return JSONResponse(
        {
            "resource": base_url,
            "authorization_servers": ["https://login.microsoftonline.com/common/v2.0"],
            "scopes_supported": ["admcms.read"],
            "bearer_methods_supported": ["header"],
        }
    )


def _build_mcp_server() -> FastMCP:
    mcp = FastMCP("admcms-mcp", stateless_http=True)

    @mcp.tool()
    def query_database(sql: str, max_rows: int = 100) -> dict:
        """Execute a read-only SELECT query against the ADMCMS database."""
        return _query_database(sql, max_rows=max_rows)

    @mcp.tool()
    def get_schema(table_name: str) -> dict:
        """Return column metadata for an ADMCMS table."""
        return _get_schema(table_name)

    @mcp.tool()
    def get_lookups(table_name: str, column_name: str, top_n: int = 50) -> dict:
        """Return up to `top_n` distinct values for a column."""
        return _get_lookups(table_name, column_name, top_n=top_n)

    @mcp.resource("admcms://schema-knowledge")
    def schema_resource() -> str:
        """Embedded knowledge about ADMCMS tables and query patterns."""
        return SCHEMA_KNOWLEDGE

    return mcp


def build_app() -> Starlette:
    mcp = _build_mcp_server()
    mcp_asgi_app = mcp.streamable_http_app()

    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", oauth_protected_resource, methods=["GET"]),
        Mount("/mcp", app=mcp_asgi_app),
    ]
    middleware = [Middleware(EntraAuthMiddleware)]
    return Starlette(routes=routes, middleware=middleware)


app = build_app()
```

- [ ] **Step 4: Run all server tests — expect pass**

Run: `pytest tests/test_server_auth.py tests/test_server_healthz.py tests/test_server_mcp.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: Entire suite green.

- [ ] **Step 6: Commit**

```bash
git add src/admcms_mcp/server.py tests/test_server_auth.py
git commit -m "feat(server): Entra JWT middleware + OAuth metadata endpoint"
```

---

### Task 13: Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Create .dockerignore**

Create `.dockerignore`:

```
.git
.gitignore
.venv
venv
__pycache__
*.pyc
.pytest_cache
.ruff_cache
.mypy_cache
.coverage
htmlcov
docs
tests
.env
.env.local
*.md
!README.md
LICENSE
files.zip
.github
infra
```

- [ ] **Step 2: Create Dockerfile**

Create `Dockerfile`:

```dockerfile
FROM python:3.12-slim-bookworm

# System deps for pyodbc + ODBC Driver 18 (Microsoft)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg apt-transport-https ca-certificates \
        unixodbc unixodbc-dev \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

EXPOSE 8000

# App Service will inject PORT env var; default to 8000 for local Docker use
ENV PORT=8000

CMD ["sh", "-c", "uvicorn admcms_mcp.server:app --host 0.0.0.0 --port $PORT"]
```

- [ ] **Step 3: Build Docker image locally**

Run: `docker build -t admcms-mcp:dev .`
Expected: Build succeeds. Final image size roughly 300-400 MB.

- [ ] **Step 4: Smoke test the container (without DB connection)**

Run:
```bash
docker run --rm -p 8000:8000 \
  -e AUTH_DISABLED=true \
  -e KEY_VAULT_URI=https://kv-dummy.vault.azure.net/ \
  -e ENTRA_APP_ID=dummy \
  -e ENTRA_ALLOWED_TENANTS=dummy \
  admcms-mcp:dev &
sleep 3
curl http://localhost:8000/healthz
```
Expected: `{"status":"ok"}`. Stop container: `docker kill $(docker ps -q --filter ancestor=admcms-mcp:dev)`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "build: Dockerfile with Python 3.12 + ODBC Driver 18"
```

---

### Task 14: Local end-to-end smoke test (optional manual check)

**🛠 Manual step — developer verifies local setup end-to-end**

- [ ] **Step 1: Create `.env` locally (gitignored)**

Copy `.env.example` to `.env`, fill in real values for local dev (using a dev Key Vault or set `AUTH_DISABLED=true` for tool-only debugging).

- [ ] **Step 2: Run `az login`**

Run: `az login`
Expected: Browser opens, you sign in with your Campai Entra identity. Required for `DefaultAzureCredential` to pick up credentials.

- [ ] **Step 3: Run the server**

Run: `uvicorn admcms_mcp.server:app --reload --port 8000`
Expected: Server starts, logs show `Application startup complete`.

- [ ] **Step 4: Smoke-test tool-logic path (auth disabled)**

In `.env`: `AUTH_DISABLED=true`. Restart server.

Run:
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```
Expected: JSON response listing `query_database`, `get_schema`, `get_lookups`.

- [ ] **Step 5: Verify OAuth metadata endpoint**

Run: `curl http://localhost:8000/.well-known/oauth-protected-resource`
Expected: JSON with `resource`, `authorization_servers`, `scopes_supported`.

- [ ] **Step 6: Note any issues**

Document any deviations (library version mismatches, MCP SDK API changes, etc.) in a follow-up commit and adjust earlier tasks.

---

## Phase 3 — Azure Infrastructure (Bicep)

### Task 15: Bicep template for App Service + Key Vault + RBAC

**Files:**
- Create: `infra/main.bicep`
- Create: `infra/main.parameters.dev.json`
- Create: `infra/main.parameters.prod.json`

- [ ] **Step 1: Write main.bicep**

Create `infra/main.bicep`:

```bicep
@description('Environment name (dev or prod)')
param environment string

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Base name, used as prefix for resource names')
param baseName string = 'admcms-mcp'

@description('Container image tag in the Azure Container Registry')
param imageTag string = 'latest'

@description('ACR login server (e.g. myregistry.azurecr.io)')
param acrLoginServer string

@description('Entra Application ID (client ID) of the admcms-mcp app registration')
param entraAppId string

@description('Comma-separated list of allowed Entra tenant IDs')
param entraAllowedTenants string

@description('Optional: Entra security group ID for Campai users allowlist')
param campaiAllowedGroup string = ''

var appServicePlanName = '${baseName}-plan-${environment}'
var appServiceName = '${baseName}-${environment}'
var keyVaultName = take('kv-${baseName}-${environment}-${uniqueString(resourceGroup().id)}', 24)

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  sku: {
    name: environment == 'prod' ? 'P1v3' : 'B1'
    tier: environment == 'prod' ? 'PremiumV3' : 'Basic'
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

resource appService 'Microsoft.Web/sites@2023-12-01' = {
  name: appServiceName
  location: location
  kind: 'app,linux,container'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${acrLoginServer}/${baseName}:${imageTag}'
      minTlsVersion: '1.2'
      alwaysOn: environment == 'prod'
      acrUseManagedIdentityCreds: true
      appSettings: [
        { name: 'WEBSITES_PORT', value: '8000' }
        { name: 'KEY_VAULT_URI', value: 'https://${keyVault.name}${az.environment().suffixes.keyvaultDns}/' }
        { name: 'ENTRA_APP_ID', value: entraAppId }
        { name: 'ENTRA_ALLOWED_TENANTS', value: entraAllowedTenants }
        { name: 'CAMPAI_ALLOWED_GROUP', value: campaiAllowedGroup }
        { name: 'AUTH_DISABLED', value: 'false' }
      ]
    }
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enabledForDeployment: false
    enabledForTemplateDeployment: false
    enabledForDiskEncryption: false
    publicNetworkAccess: 'Enabled'
  }
}

// Grant App Service's Managed Identity read access to secrets
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, appService.id, keyVaultSecretsUserRoleId)
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: appService.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grant App Service access to pull images from ACR
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: split(acrLoginServer, '.')[0]
}
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource acrRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, appService.id, acrPullRoleId)
  properties: {
    roleDefinitionId: resourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: appService.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output appServiceName string = appService.name
output appServiceUrl string = 'https://${appService.properties.defaultHostName}'
output keyVaultName string = keyVault.name
output appServicePrincipalId string = appService.identity.principalId
```

- [ ] **Step 2: Write parameters files**

Create `infra/main.parameters.dev.json`:

```json
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "environment": { "value": "dev" },
    "baseName": { "value": "admcms-mcp" },
    "imageTag": { "value": "latest" },
    "acrLoginServer": { "value": "<acr-name>.azurecr.io" },
    "entraAppId": { "value": "<filled-in-after-app-registration>" },
    "entraAllowedTenants": { "value": "<campai-tenant-id>,<cms-lelystad-tenant-id>" },
    "campaiAllowedGroup": { "value": "" }
  }
}
```

Create `infra/main.parameters.prod.json` (same structure, different values — production-specific).

- [ ] **Step 3: Validate Bicep compiles**

Run: `az bicep build --file infra/main.bicep`
Expected: `infra/main.json` produced without errors.

- [ ] **Step 4: Commit**

```bash
git add infra/main.bicep infra/main.parameters.dev.json infra/main.parameters.prod.json
git commit -m "infra: Bicep template for App Service + Key Vault + RBAC"
```

---

### Task 16 🛠: Manual — create Entra app registration + Azure resource group + ACR

**🛠 User action required. Document commands that user/developer will run with owner privileges.**

- [ ] **Step 1: Log in to Azure CLI with Campai tenant**

Run: `az login --tenant <campai-tenant-id>`

- [ ] **Step 2: Create resource group**

Run:
```bash
az group create --name rg-admcms-mcp-dev --location westeurope
```

- [ ] **Step 3: Create Azure Container Registry**

Run:
```bash
az acr create \
  --resource-group rg-admcms-mcp-dev \
  --name campaiadmcmsmcp \
  --sku Basic \
  --admin-enabled false
```

Note the login server: `campaiadmcmsmcp.azurecr.io`. Update `infra/main.parameters.dev.json` with this value.

- [ ] **Step 4: Create Entra App Registration**

Run:
```bash
az ad app create \
  --display-name "admcms-mcp" \
  --sign-in-audience AzureADMultipleOrgs \
  --identifier-uris "api://admcms-mcp"
```

Capture the `appId` from output (this is `ENTRA_APP_ID`).

- [ ] **Step 5: Expose API scope `admcms.read`**

Run (replace `<APP_ID>`):
```bash
APP_ID=<appId-from-step-4>
az ad app update --id $APP_ID --set api='{
  "oauth2PermissionScopes":[{
    "id":"'$(uuidgen)'",
    "adminConsentDescription":"Read ADMCMS database via MCP server",
    "adminConsentDisplayName":"Read ADMCMS",
    "isEnabled":true,
    "type":"User",
    "userConsentDescription":"Allows the app to read ADMCMS data on your behalf",
    "userConsentDisplayName":"Read ADMCMS",
    "value":"admcms.read"
  }]
}'
```

- [ ] **Step 6: Gather tenant IDs**

Run:
```bash
az account show --query tenantId -o tsv  # Campai tenant
```

Request the CMS Lelystad tenant ID from their IT team, or discover it via `https://login.microsoftonline.com/<cms-domain>/.well-known/openid-configuration` (issuer claim contains their tenant ID).

Update `infra/main.parameters.dev.json`:
- `entraAppId` = appId from step 4
- `entraAllowedTenants` = "<campai-tenant-id>,<cms-lelystad-tenant-id>"

- [ ] **Step 7: Commit parameter updates**

```bash
git add infra/main.parameters.dev.json
git commit -m "infra: fill in ACR, Entra app ID, and tenant IDs for dev"
```

- [ ] **Step 8: (Optional for now) Create Entra security group for Campai users**

In Azure Portal → Entra ID → Groups → New group:
- Type: Security
- Name: `admcms-users`
- Add members: Campai employees who should have access

Note the group Object ID. Update `campaiAllowedGroup` in parameter file.

---

### Task 17: First deploy of infrastructure

**🛠 Partially manual — uses `az` CLI; agent can run if credentials present, but typically user runs.**

- [ ] **Step 1: Deploy Bicep to dev resource group**

Run:
```bash
az deployment group create \
  --resource-group rg-admcms-mcp-dev \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.dev.json
```

Expected: Deployment succeeds in ~5 minutes. Note the outputs: `appServiceName`, `appServiceUrl`, `keyVaultName`.

- [ ] **Step 2: Put secrets in Key Vault**

Run (replace placeholders):
```bash
KV_NAME=<keyVaultName-from-output>

# Grant yourself Secrets Officer role temporarily to write secrets
USER_OID=$(az ad signed-in-user show --query id -o tsv)
az role assignment create \
  --assignee $USER_OID \
  --role "Key Vault Secrets Officer" \
  --scope $(az keyvault show -n $KV_NAME --query id -o tsv)

# DB credentials (existing, cannot be rotated)
az keyvault secret set --vault-name $KV_NAME --name admcms-db-username --value "<existing-username>"
az keyvault secret set --vault-name $KV_NAME --name admcms-db-password --value "<existing-password>"

# Allowlist (initial: empty, update as users join)
az keyvault secret set --vault-name $KV_NAME --name admcms-allowlist --value '{"users":[],"groups":[]}'
```

- [ ] **Step 3: Verify App Service Managed Identity can read Key Vault**

Run: `az webapp show --name admcms-mcp-dev --resource-group rg-admcms-mcp-dev --query identity.principalId -o tsv`

Then verify that principalId has `Key Vault Secrets User` role on the Key Vault in Portal → Key Vault → Access control (IAM).

- [ ] **Step 4: Note outbound IPs for DB firewall**

Run:
```bash
az webapp show \
  --name admcms-mcp-dev \
  --resource-group rg-admcms-mcp-dev \
  --query possibleOutboundIpAddresses \
  -o tsv
```

Expected: comma-separated list of ~4-8 IPs. These need to be added to CMS Lelystad SQL firewall allowlist (user coordinates this with CMS).

- [ ] **Step 5: Document the deployment**

Create `docs/deployment.md` with the commands from Tasks 16-17 for future reference. Commit:

```bash
git add docs/deployment.md
git commit -m "docs: Azure deployment runbook"
```

---

### Task 18 🛠: User adds App Service outbound IPs to CMS Lelystad SQL firewall

**🛠 Manual — user coordinates with CMS Lelystad admin.**

- [ ] **Step 1: Share outbound IPs with CMS**

Send the IPs from Task 17 Step 4 to CMS Lelystad admin. Request they add all IPs to the ADMCMS Azure SQL server firewall (Portal → SQL Server → Networking → Public access → Firewall rules, or CLI `az sql server firewall-rule create`).

- [ ] **Step 2: Verify connectivity**

Once CMS confirms firewall is updated, test from the App Service:
```bash
az webapp ssh --name admcms-mcp-dev --resource-group rg-admcms-mcp-dev
# inside the container shell:
python -c "import pyodbc; print(pyodbc.drivers())"
```

Expected: `['ODBC Driver 18 for SQL Server']` or similar. Exit SSH: `exit`.

---

## Phase 4 — CI/CD

### Task 19: GitHub Actions CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write ci.yml**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: Install Microsoft ODBC Driver 18
        run: |
          curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor -o /usr/share/keyrings/microsoft.gpg
          echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/ubuntu/22.04/prod jammy main" | sudo tee /etc/apt/sources.list.d/mssql-release.list
          sudo apt-get update
          sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev

      - name: Install project
        run: |
          pip install --upgrade pip
          pip install -e ".[dev]"

      - name: Ruff lint
        run: ruff check src tests

      - name: Ruff format check
        run: ruff format --check src tests

      - name: Run tests with coverage
        run: pytest --cov=admcms_mcp --cov-report=term-missing --cov-fail-under=70

      - name: Dependency vulnerability scan
        run: pip-audit --ignore-vuln PYSEC-2022-42969 --skip-editable || true
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: GitHub Actions workflow for lint, test, security scan"
```

---

### Task 20: GitHub Actions deploy workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: Write deploy.yml**

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy

on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      environment:
        description: 'Deployment environment'
        type: choice
        options: [dev, prod]
        default: dev

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: ${{ github.event.inputs.environment || 'dev' }}
    permissions:
      id-token: write
      contents: read

    steps:
      - uses: actions/checkout@v4

      - name: Azure login (OIDC)
        uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}

      - name: Deploy Bicep infrastructure
        run: |
          az deployment group create \
            --resource-group rg-admcms-mcp-${{ github.event.inputs.environment || 'dev' }} \
            --template-file infra/main.bicep \
            --parameters infra/main.parameters.${{ github.event.inputs.environment || 'dev' }}.json \
            --parameters imageTag=${{ github.sha }}

      - name: Build and push Docker image
        run: |
          ACR_NAME=$(az acr list --resource-group rg-admcms-mcp-${{ github.event.inputs.environment || 'dev' }} --query "[0].name" -o tsv)
          az acr login --name $ACR_NAME
          docker build -t $ACR_NAME.azurecr.io/admcms-mcp:${{ github.sha }} -t $ACR_NAME.azurecr.io/admcms-mcp:latest .
          docker push $ACR_NAME.azurecr.io/admcms-mcp:${{ github.sha }}
          docker push $ACR_NAME.azurecr.io/admcms-mcp:latest

      - name: Restart App Service to pull new image
        run: |
          az webapp restart \
            --name admcms-mcp-${{ github.event.inputs.environment || 'dev' }} \
            --resource-group rg-admcms-mcp-${{ github.event.inputs.environment || 'dev' }}

      - name: Wait for healthy endpoint
        run: |
          URL=https://admcms-mcp-${{ github.event.inputs.environment || 'dev' }}.azurewebsites.net/healthz
          for i in {1..30}; do
            if curl -sf $URL; then
              echo "Healthy after $i attempts"
              exit 0
            fi
            sleep 10
          done
          echo "Never became healthy"
          exit 1
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: deploy workflow — build image, push to ACR, restart App Service"
```

---

### Task 21 🛠: Configure GitHub secrets + federated credential

**🛠 Manual — requires GitHub org admin + Azure tenant admin.**

- [ ] **Step 1: Create Azure service principal with federated credential**

Run:
```bash
SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

# Create SP
SP=$(az ad sp create-for-rbac \
  --name "gh-admcms-mcp-deploy" \
  --role "Contributor" \
  --scopes /subscriptions/$SUB_ID/resourceGroups/rg-admcms-mcp-dev \
  --json-auth)
CLIENT_ID=$(echo $SP | jq -r .clientId)

# Add federated credential for GitHub Actions
az ad app federated-credential create \
  --id $CLIENT_ID \
  --parameters '{
    "name": "github-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<GH_ORG>/<GH_REPO>:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'

# Also allow deploys triggered by workflow_dispatch on environment=dev
az ad app federated-credential create \
  --id $CLIENT_ID \
  --parameters '{
    "name": "github-env-dev",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<GH_ORG>/<GH_REPO>:environment:dev",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

Replace `<GH_ORG>` and `<GH_REPO>`. Capture `$CLIENT_ID`, `$TENANT_ID`, `$SUB_ID` for secrets.

- [ ] **Step 2: Add SP permissions to ACR + App Service**

Run:
```bash
az role assignment create \
  --assignee $CLIENT_ID \
  --role "AcrPush" \
  --scope $(az acr show -n campaiadmcmsmcp --query id -o tsv)

az role assignment create \
  --assignee $CLIENT_ID \
  --role "Website Contributor" \
  --scope /subscriptions/$SUB_ID/resourceGroups/rg-admcms-mcp-dev
```

- [ ] **Step 3: Set GitHub secrets**

In GitHub repo → Settings → Secrets and variables → Actions → New repository secret:

- `AZURE_CLIENT_ID` = `$CLIENT_ID`
- `AZURE_TENANT_ID` = `$TENANT_ID`
- `AZURE_SUBSCRIPTION_ID` = `$SUB_ID`

Also create GitHub Environments: `dev` and `prod` (Settings → Environments → New). Add environment protection rule for `prod`: require manual approval.

---

## Phase 5 — Production Deployment & E2E

### Task 22: First production-like deploy and health check

- [ ] **Step 1: Push current main to GitHub**

Run:
```bash
git remote add origin git@github.com:<GH_ORG>/<GH_REPO>.git  # if not already
git push -u origin main
```

- [ ] **Step 2: Observe CI workflow**

Navigate to GitHub Actions tab. Verify `CI` workflow passes (lint + tests).

- [ ] **Step 3: Observe deploy workflow**

Verify `Deploy` workflow succeeds:
- Bicep deployment green
- Docker build + push green
- App Service restart + health check succeeds

- [ ] **Step 4: Verify /healthz from internet**

Run: `curl https://admcms-mcp-dev.azurewebsites.net/healthz`
Expected: `{"status":"ok"}`.

- [ ] **Step 5: Verify OAuth metadata endpoint**

Run: `curl https://admcms-mcp-dev.azurewebsites.net/.well-known/oauth-protected-resource`
Expected: JSON with correct `resource` URL.

---

### Task 23 🛠: Update allowlist with real users

**🛠 Manual — you decide who gets access.**

- [ ] **Step 1: Add your own OID to allowlist**

Find your OID:
```bash
az ad signed-in-user show --query id -o tsv
```

Update Key Vault:
```bash
KV_NAME=<name-from-deployment>
YOUR_OID=<oid-from-above>
az keyvault secret set \
  --vault-name $KV_NAME \
  --name admcms-allowlist \
  --value '{"users":["'$YOUR_OID'"],"groups":[]}'
```

(Allowlist cache is 5 min; either wait or restart App Service to pick up immediately.)

- [ ] **Step 2: (Optional) Create Campai security group and add members**

Portal → Entra ID → Groups → New group `admcms-users`. Add Campai employees. Note the group Object ID. Update `campaiAllowedGroup` parameter in `infra/main.parameters.dev.json`. Re-deploy via GitHub Actions manual trigger.

---

### Task 24 🛠: End-to-end test with Claude Desktop

**🛠 Manual — user validates remote MCP works.**

- [ ] **Step 1: Configure Claude Desktop with remote MCP**

In Claude Desktop → Settings → MCP Servers → Add server:
- URL: `https://admcms-mcp-dev.azurewebsites.net/mcp`
- Transport: HTTP (Streamable)

- [ ] **Step 2: Trigger first use**

Start a new chat, ask: "List the tables in ADMCMS."
Expected: Claude Desktop opens a browser for Entra SSO login. Complete login.
After login: Claude receives bearer token and retries the call. Tool `get_schema` (or similar) executes; list of tables returned.

- [ ] **Step 3: Test query tool**

Ask Claude: "Show me the first 5 ARTIKEL rows."
Expected: Claude calls `query_database` with an appropriate `SELECT TOP 5 * FROM ARTIKEL`, results returned.

- [ ] **Step 4: Test auth rejection**

Remove your OID from the allowlist, restart App Service, retry a query. Expected: Claude surfaces a 403 / "forbidden" message. Re-add yourself.

- [ ] **Step 5: Document known limitations**

Add any Claude Desktop quirks (redirect URI setup, first-time auth UX) to `docs/handleiding.md`.

---

### Task 25: Finalize documentation + cleanup legacy files

**Files:**
- Create: `README.md`
- Modify: `docs/handleiding.md` (renamed from `handleiding_admcms_mcp.md`)
- Modify: `docs/deployment.md`
- Delete: `server.py` (old monolith, replaced by `src/admcms_mcp/`)
- Delete: `test_connection.py` (replaced by pytest suite)
- Delete: `start_server.bat` (stdio mode removed)
- Delete: `requirements.txt` (replaced by pyproject.toml)
- Delete: `__pycache__/` (gitignored)
- Delete: `LICENSE` (evaluate: keep MIT with updated copyright, or replace)

- [ ] **Step 1: Write README.md**

Create `README.md`:

```markdown
# admcms-mcp

Remote-capable MCP server that exposes the ADMCMS (CMS Lelystad automotive parts database) to AI agents via Entra ID SSO.

## Features

- 3 read-only tools: `query_database`, `get_schema`, `get_lookups`
- MCP Streamable HTTP transport (MCP spec 2025-03-26)
- OAuth 2.1 Resource Server, Entra ID multi-tenant
- Hybrid allowlist: Entra security group + Key Vault JSON
- Azure App Service + Key Vault + Managed Identity

## Quick Start (local development)

Requirements: Python 3.12+, ODBC Driver 18 for SQL Server, Azure CLI.

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv/Scripts/activate on Windows
pip install -e ".[dev]"
cp .env.example .env
# Fill .env with real values or set AUTH_DISABLED=true for tool-only debugging
az login
uvicorn admcms_mcp.server:app --reload --port 8000
```

Test: `curl http://localhost:8000/healthz`

## Deployment

See [docs/deployment.md](docs/deployment.md).

## Architecture

See [docs/superpowers/specs/2026-04-18-admcms-mcp-remote-design.md](docs/superpowers/specs/2026-04-18-admcms-mcp-remote-design.md).

## Tests

`pytest -v --cov=admcms_mcp`
```

- [ ] **Step 2: Move handleiding**

Run: `git mv handleiding_admcms_mcp.md docs/handleiding.md`

- [ ] **Step 3: Update handleiding.md content for remote mode**

Edit `docs/handleiding.md`: remove stdio/Claude Desktop-spawn sections, add "Remote MCP server setup" section explaining:
- URL: `https://admcms-mcp-dev.azurewebsites.net/mcp`
- Login via SSO on first use
- Who to contact for allowlist requests

- [ ] **Step 4: Move DATABASE-KENNIS**

Run: `git mv DATABASE-KENNIS docs/DATABASE-KENNIS`

- [ ] **Step 5: Delete legacy files**

Run:
```bash
git rm server.py test_connection.py start_server.bat requirements.txt
rm -rf __pycache__
```

- [ ] **Step 6: Evaluate LICENSE**

Inspect current `LICENSE`. If it's a generic MIT with `tuxmanro` copyright, update copyright to "Campai B.V." and keep MIT. If it's something else, decide whether to keep, replace, or remove for a private internal project.

- [ ] **Step 7: Run full test suite as final check**

Run: `pytest -v --cov=admcms_mcp`
Expected: All tests PASS, coverage >70% overall, >80% on `auth.py` and `db.py`.

- [ ] **Step 8: Commit and push**

```bash
git add README.md docs/ LICENSE
git commit -m "docs: finalize README, runbook, and remote-mode handleiding; remove legacy files"
git push origin main
```

- [ ] **Step 9: Tag release**

```bash
git tag -a v1.0.0 -m "First remote-capable release"
git push origin v1.0.0
```

---

## Done — Verification Checklist

- [ ] All pytest tests pass (`pytest -v`)
- [ ] Ruff lint + format clean (`ruff check src tests && ruff format --check src tests`)
- [ ] Docker image builds (`docker build -t admcms-mcp:dev .`)
- [ ] Bicep compiles (`az bicep build --file infra/main.bicep`)
- [ ] GitHub Actions CI workflow passes on `main`
- [ ] GitHub Actions deploy workflow deploys successfully
- [ ] `/healthz` reachable over HTTPS
- [ ] `/.well-known/oauth-protected-resource` returns correct metadata
- [ ] `/mcp` returns 401 with `WWW-Authenticate` when no token
- [ ] `/mcp` with valid token + allowlisted user executes tool calls
- [ ] `/mcp` with valid token but non-allowlisted user returns 403
- [ ] App Service outbound IPs shared with CMS Lelystad; SQL firewall updated
- [ ] Claude Desktop successfully queries ADMCMS via remote MCP
- [ ] README, handleiding, deployment runbook all up-to-date
- [ ] Legacy files (`server.py`, `start_server.bat`, etc.) removed
- [ ] v1.0.0 tag pushed
