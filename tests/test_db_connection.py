from unittest.mock import MagicMock, patch

import pytest

import admcms_mcp.db as _db
from admcms_mcp.db import execute_query, get_schema_columns


@pytest.fixture(autouse=True)
def _clear_query_cache():
    # The in-process result cache would otherwise leak across cases (a repeated
    # (sql, params) would return a cached list and skip the mocked cursor).
    _db._query_cache.clear()
    yield
    _db._query_cache.clear()


def _mock_borrow(mock_borrow, mock_conn):
    """Wire a patched _borrow() context manager to yield `mock_conn`."""
    mock_borrow.return_value.__enter__.return_value = mock_conn
    mock_borrow.return_value.__exit__.return_value = False


class TestExecuteQuery:
    @patch("admcms_mcp.db._borrow")
    def test_execute_query_returns_rows_as_dicts(self, mock_borrow):
        mock_cursor = MagicMock()
        mock_cursor.description = [("id", None), ("name", None)]
        mock_cursor.fetchmany.return_value = [(1, "foo"), (2, "bar")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        _mock_borrow(mock_borrow, mock_conn)

        rows = execute_query("SELECT id, name FROM foo", max_rows=10)

        assert rows == [{"id": 1, "name": "foo"}, {"id": 2, "name": "bar"}]
        mock_cursor.execute.assert_called_once_with("SELECT id, name FROM foo")
        # cap + 1: the extra row is the truncation probe, trimmed before returning.
        mock_cursor.fetchmany.assert_called_once_with(11)

    @patch("admcms_mcp.db._borrow")
    def test_execute_query_rejects_dangerous_query(self, mock_borrow):
        with pytest.raises(ValueError, match="INSERT"):
            execute_query("INSERT INTO foo VALUES (1)")
        mock_borrow.assert_not_called()

    @patch("admcms_mcp.db._borrow")
    def test_execute_query_caps_max_rows_at_the_hard_limit(self, mock_borrow):
        mock_cursor = MagicMock()
        mock_cursor.description = [("id", None)]
        mock_cursor.fetchmany.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        _mock_borrow(mock_borrow, mock_conn)

        execute_query("SELECT id FROM foo WHERE id = 987654321", max_rows=99999)

        from admcms_mcp.db import _MAX_ROWS_HARD_LIMIT

        mock_cursor.fetchmany.assert_called_once_with(_MAX_ROWS_HARD_LIMIT + 1)


class TestGetSchemaColumns:
    @patch("admcms_mcp.db._borrow")
    def test_get_schema_uses_parameterized_query(self, mock_borrow):
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
        _mock_borrow(mock_borrow, mock_conn)

        result = get_schema_columns("ARTIKEL")

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

    @patch("admcms_mcp.db._borrow")
    def test_get_schema_injection_attempt_does_not_inject(self, mock_borrow):
        mock_cursor = MagicMock()
        mock_cursor.description = [("COLUMN_NAME", None)]
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        _mock_borrow(mock_borrow, mock_conn)

        get_schema_columns("foo'; DROP TABLE bar--")

        called_sql, called_params = mock_cursor.execute.call_args[0]
        assert called_params == ("foo'; DROP TABLE bar--",)
        assert "DROP" not in called_sql.upper()


class TestExecuteQueryParams:
    @patch("admcms_mcp.db._borrow")
    def test_passes_params_to_cursor(self, mock_borrow):
        from admcms_mcp.db import execute_query_params

        mock_cursor = MagicMock()
        mock_cursor.description = [("id", None)]
        mock_cursor.fetchmany.return_value = [(42,)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        _mock_borrow(mock_borrow, mock_conn)

        rows = execute_query_params("SELECT id FROM t WHERE x = ?", ("abc",), max_rows=5)

        mock_cursor.execute.assert_called_once_with("SELECT id FROM t WHERE x = ?", ("abc",))
        mock_cursor.fetchmany.assert_called_once_with(6)  # cap + truncation probe
        assert rows == [{"id": 42}]

    @patch("admcms_mcp.db._borrow")
    def test_rejects_dangerous_query(self, mock_borrow):
        from admcms_mcp.db import execute_query_params

        with pytest.raises(ValueError, match="INSERT"):
            execute_query_params("INSERT INTO t VALUES (?)", ("x",))
        mock_borrow.assert_not_called()
