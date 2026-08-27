"""The query cache must not grow without end, and must not cache the wrong things.

Keys are `(sql, params, row cap)` and `query_database` puts arbitrary SQL on top
of that, so an unbounded dict is a slow leak in a 1 GiB container with an OOM at
the end of it.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from admcms_mcp import db


@pytest.fixture(autouse=True)
def _clean_cache():
    db.clear_query_cache()
    yield
    db.clear_query_cache()


@pytest.fixture
def rows():
    """A pool that returns one row for whatever it is asked."""
    cursor = MagicMock()
    cursor.description = [("id", None)]
    cursor.fetchmany.return_value = [(1,)]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    with patch("admcms_mcp.db._borrow") as borrow:
        borrow.return_value.__enter__.return_value = conn
        borrow.return_value.__exit__.return_value = False
        yield conn


class TestEviction:
    def test_the_cache_stops_growing_at_the_ceiling(self, rows):
        for i in range(db._QUERY_CACHE_MAX_ENTRIES + 50):
            db.execute_query(f"SELECT {i} AS id FROM t")

        assert db.cache_size() == db._QUERY_CACHE_MAX_ENTRIES

    def test_the_least_recently_used_entry_is_the_one_that_goes(self, rows):
        db.execute_query("SELECT 0 AS id FROM t")
        for i in range(1, db._QUERY_CACHE_MAX_ENTRIES):
            db.execute_query(f"SELECT {i} AS id FROM t")

        # Touch the oldest so it is no longer the least recently used...
        db.execute_query("SELECT 0 AS id FROM t")
        # ...then push one past the ceiling.
        db.execute_query("SELECT 9999 AS id FROM t")

        assert db._cache_get(("SELECT 0 AS id FROM t", None, 100)) is not None
        assert db._cache_get(("SELECT 1 AS id FROM t", None, 100)) is None

    def test_expired_entries_are_dropped_on_a_miss(self, rows, monkeypatch):
        db.execute_query("SELECT 1 AS id FROM t")
        assert db.cache_size() == 1

        # Jump past the TTL without sleeping through it.
        later = time.monotonic() + db._QUERY_CACHE_TTL_SECONDS + 1
        monkeypatch.setattr(db.time, "monotonic", lambda: later)

        db._cache_get(("something", "else", 100))  # a miss

        assert db.cache_size() == 0, "a stale entry must not survive on a quiet server"

    def test_an_expired_entry_is_not_served(self, rows, monkeypatch):
        db.execute_query("SELECT 1 AS id FROM t")
        later = time.monotonic() + db._QUERY_CACHE_TTL_SECONDS + 1
        monkeypatch.setattr(db.time, "monotonic", lambda: later)

        assert db._cache_get(("SELECT 1 AS id FROM t", None, 100)) is None


class TestWhatGetsCached:
    def test_a_repeated_fixed_query_is_served_from_the_cache(self, rows):
        db.execute_query("SELECT 1 AS id FROM t")
        db.execute_query("SELECT 1 AS id FROM t")

        assert rows.cursor.call_count == 1, "the second call should not reach the database"

    def test_free_form_sql_is_not_cached(self, rows):
        db.execute_query("SELECT 1 AS id FROM t", use_cache=False)
        db.execute_query("SELECT 1 AS id FROM t", use_cache=False)

        assert db.cache_size() == 0
        assert rows.cursor.call_count == 2

    def test_query_database_asks_for_no_caching(self):
        """Every free-form statement differs; caching them only collects keys."""
        with patch("admcms_mcp.tools.execute_query") as execute:
            execute.return_value = []
            from admcms_mcp.tools import query_database

            query_database("SELECT 1", max_rows=10)

        assert execute.call_args.kwargs["use_cache"] is False


def test_cache_key_is_not_per_user(rows):
    """Stated out loud: results are shared between callers.

    Correct today — everyone on the allowlist may see the same rows, and sharing
    is what makes the cache worth having. The day a tool filters per customer,
    this test fails to compile in someone's head first: the key must grow a
    subject, or that tool must opt out.
    """
    db.execute_query("SELECT 1 AS id FROM t")

    (key,) = list(db._query_cache)

    assert key == ("SELECT 1 AS id FROM t", None, 100)
    assert len(key) == 3, "sql, params, row cap — no identity in the key"
