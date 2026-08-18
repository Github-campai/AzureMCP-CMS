"""Row caps must announce themselves.

Before this, `cursor.fetchmany(n)` cut result sets off silently and a caller had
no way to tell a complete answer from a partial one.
"""

from unittest.mock import MagicMock, patch

import pytest

from admcms_mcp import db


@pytest.fixture(autouse=True)
def _clean_state():
    db.reset_truncation()
    db._query_cache.clear()
    yield
    db.reset_truncation()
    db._query_cache.clear()


def _fake_conn(row_count: int):
    """A connection whose cursor yields `row_count` single-column rows."""
    cursor = MagicMock()
    cursor.description = [("value",)]
    cursor.fetchmany.side_effect = lambda n: [(i,) for i in range(min(n, row_count))]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _patch_borrow(conn):
    from contextlib import contextmanager

    @contextmanager
    def _borrow():
        yield conn

    return patch.object(db, "_borrow", _borrow)


def test_exactly_the_cap_is_not_reported_as_truncated():
    """The subtle case: n rows for a cap of n is complete, not cut off."""
    with _patch_borrow(_fake_conn(10)):
        rows = db.execute_query("SELECT 1", max_rows=10)
    assert len(rows) == 10
    assert db.was_truncated() is False


def test_more_rows_than_the_cap_is_reported():
    with _patch_borrow(_fake_conn(50)):
        rows = db.execute_query("SELECT 1", max_rows=10)
    assert len(rows) == 10, "must still trim to the cap"
    assert db.was_truncated() is True


def test_fewer_rows_than_the_cap_is_complete():
    with _patch_borrow(_fake_conn(3)):
        rows = db.execute_query("SELECT 1", max_rows=10)
    assert len(rows) == 3
    assert db.was_truncated() is False


def test_parameterised_query_reports_truncation_too():
    with _patch_borrow(_fake_conn(50)):
        db.execute_query_params("SELECT 1 WHERE x = ?", (1,), max_rows=5)
    assert db.was_truncated() is True


def test_cache_hit_still_reports_truncation():
    """A second caller must not be told a trimmed cached result is complete."""
    with _patch_borrow(_fake_conn(50)):
        db.execute_query("SELECT 1", max_rows=10)
    assert db.was_truncated() is True

    db.reset_truncation()
    with _patch_borrow(_fake_conn(50)) as _:
        rows = db.execute_query("SELECT 1", max_rows=10)  # served from cache
    assert len(rows) == 10
    assert db.was_truncated() is True, "cache dropped the truncation flag"


def test_reset_clears_the_flag():
    with _patch_borrow(_fake_conn(50)):
        db.execute_query("SELECT 1", max_rows=10)
    db.reset_truncation()
    assert db.was_truncated() is False


def test_caller_asking_above_the_old_500_cap_is_honoured():
    """get_shipping_cost_analysis asks for 2000 and used to receive 500."""
    assert db._MAX_ROWS_HARD_LIMIT >= 2000
    with _patch_borrow(_fake_conn(1500)):
        rows = db.execute_query("SELECT 1", max_rows=2000)
    assert len(rows) == 1500
    assert db.was_truncated() is False


def test_distinct_values_reports_truncation():
    cursor = MagicMock()
    cursor.fetchall.return_value = [(i,) for i in range(11)]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    with _patch_borrow(conn):
        values = db.get_distinct_values("PRODUCT", "Make", top_n=10)
    assert len(values) == 10
    assert db.was_truncated() is True
