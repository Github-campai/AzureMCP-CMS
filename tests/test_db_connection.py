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

        get_schema_columns("foo'; DROP TABLE bar--")

        called_sql, called_params = mock_cursor.execute.call_args[0]
        assert called_params == ("foo'; DROP TABLE bar--",)
        assert "DROP" not in called_sql.upper()


class TestExecuteQueryParams:
    @patch("admcms_mcp.db._get_connection")
    def test_passes_params_to_cursor(self, mock_get_conn):
        from admcms_mcp.db import execute_query_params

        mock_cursor = MagicMock()
        mock_cursor.description = [("id", None)]
        mock_cursor.fetchmany.return_value = [(42,)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        rows = execute_query_params("SELECT id FROM t WHERE x = ?", ("abc",), max_rows=5)

        mock_cursor.execute.assert_called_once_with("SELECT id FROM t WHERE x = ?", ("abc",))
        mock_cursor.fetchmany.assert_called_once_with(5)
        assert rows == [{"id": 42}]

    @patch("admcms_mcp.db._get_connection")
    def test_rejects_dangerous_query(self, mock_get_conn):
        import pytest

        from admcms_mcp.db import execute_query_params

        with pytest.raises(ValueError, match="INSERT"):
            execute_query_params("INSERT INTO t VALUES (?)", ("x",))
        mock_get_conn.assert_not_called()
