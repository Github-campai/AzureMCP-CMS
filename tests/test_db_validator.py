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
        with pytest.raises(ValueError, match="INSERT"):
            validate_query("SELECT 1; INSERT INTO foo VALUES (1)")

    def test_rejects_dangerous_keyword_in_comment_stripped(self):
        with pytest.raises(ValueError):
            validate_query("SELECT 1 /* INSERT */")

    def test_dangerous_keyword_substring_does_not_match(self):
        validate_query("SELECT * FROM foo WHERE col = 'INSERTED_VALUE'")
        validate_query("SELECT DROPDOWN FROM foo")
