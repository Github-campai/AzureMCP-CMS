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
        # Caught by the semicolon rule now, which fires before the keyword scan:
        # stacked statements are refused as a shape, not because of what the
        # second one happens to say.
        with pytest.raises(ValueError, match="semicolon"):
            validate_query("SELECT 1; INSERT INTO foo VALUES (1)")

    def test_rejects_dangerous_keyword_in_comment_stripped(self):
        with pytest.raises(ValueError):
            validate_query("SELECT 1 /* INSERT */")

    def test_dangerous_keyword_substring_does_not_match(self):
        validate_query("SELECT * FROM foo WHERE col = 'INSERTED_VALUE'")
        validate_query("SELECT DROPDOWN FROM foo")


class TestSemicolonsAndLiterals:
    """Bevinding 10: reject stacked statements outright, and stop refusing data."""

    def test_a_trailing_semicolon_is_fine(self):
        validate_query("SELECT 1 FROM t;")
        validate_query("SELECT 1 FROM t;   ")

    def test_a_semicolon_between_statements_is_refused(self):
        with pytest.raises(ValueError, match="semicolon"):
            validate_query("SELECT 1; SELECT 2")

    def test_a_semicolon_inside_a_string_is_data(self):
        validate_query("SELECT * FROM Product WHERE Description = 'bolt; washer'")

    def test_a_keyword_inside_a_string_is_data(self):
        """A product called 'update kit' is not an UPDATE statement."""
        validate_query("SELECT * FROM Product WHERE Searchname = 'update kit'")

    def test_an_escaped_quote_does_not_open_a_hole(self):
        with pytest.raises(ValueError, match="DROP"):
            validate_query("SELECT * FROM t WHERE a = 'it''s' AND DROP")

    def test_the_message_says_what_was_refused_and_what_to_do(self):
        with pytest.raises(ValueError) as excinfo:
            validate_query("SELECT * FROM t WHERE UPDATE = 1")
        message = str(excinfo.value)
        assert "UPDATE" in message
        assert "single quotes" in message, "a refusal should explain the way out"

    def test_a_write_statement_names_the_database_rights(self):
        with pytest.raises(ValueError) as excinfo:
            validate_query("DELETE FROM Product")
        assert "read-only" in str(excinfo.value)
