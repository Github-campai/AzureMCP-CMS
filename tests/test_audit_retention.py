"""Retention: the trail is kept as long as it is useful, and no longer.

An audit row carries the UPN of the person who ran the query, so keeping it
forever is not neutral — the AVG asks for a term and a way to enforce it. These
tests pin down the term, the switch that disables it, and that the purge really
removes rows in both stores.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from admcms_mcp import audit
from admcms_mcp.audit import AuditEntry
from admcms_mcp.audit_stores import AzureTableStore, LocalSQLiteStore, NullStore


def _entry_at(when: datetime, **kwargs) -> AuditEntry:
    stamp = when.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return AuditEntry(timestamp=stamp, tool_name="get_revenue", user="iemand@campai.nl", **kwargs)


@pytest.fixture
def sqlite_store(tmp_path) -> LocalSQLiteStore:
    return LocalSQLiteStore(path=str(tmp_path / "audit.db"))


class TestConfiguredTerm:
    def test_default_is_one_year(self, monkeypatch):
        monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)
        assert audit.retention_days() == 365

    def test_env_overrides_it(self, monkeypatch):
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "90")
        assert audit.retention_days() == 90

    def test_zero_disables_purging(self, monkeypatch):
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "0")
        assert audit.retention_days() == 0

    def test_nonsense_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "ooit")
        assert audit.retention_days() == 365


class TestSqlitePurge:
    def test_entries_past_the_term_are_removed(self, sqlite_store):
        now = datetime.now(UTC)
        sqlite_store.log(_entry_at(now - timedelta(days=400)))
        sqlite_store.log(_entry_at(now - timedelta(days=10)))

        removed = sqlite_store.purge_older_than(365)

        assert removed == 1
        remaining = sqlite_store.query(limit=10)
        assert len(remaining) == 1

    def test_entries_inside_the_term_stay(self, sqlite_store):
        now = datetime.now(UTC)
        sqlite_store.log(_entry_at(now - timedelta(days=364)))

        assert sqlite_store.purge_older_than(365) == 0
        assert len(sqlite_store.query(limit=10)) == 1

    def test_zero_days_purges_nothing(self, sqlite_store):
        sqlite_store.log(_entry_at(datetime.now(UTC) - timedelta(days=4000)))

        assert sqlite_store.purge_older_than(0) == 0
        assert len(sqlite_store.query(limit=10)) == 1


class FakeDeletingTableClient:
    """Table client that also honours PartitionKey-range filters and deletes."""

    def __init__(self, fail_on: str | None = None):
        self.entities: list[dict] = []
        self.filters: list[str | None] = []
        self.deleted: list[tuple[str, str]] = []
        self.fail_on = fail_on

    def upsert_entity(self, entity):
        self.entities.append(dict(entity))

    def query_entities(self, query_filter=None, select=None):
        self.filters.append(query_filter)
        rows = list(self.entities)
        if query_filter and "PartitionKey lt" in query_filter:
            cutoff = query_filter.split("'")[1]
            rows = [r for r in rows if r["PartitionKey"] < cutoff]
        return rows

    def delete_entity(self, partition_key: str, row_key: str):
        if self.fail_on and row_key == self.fail_on:
            raise RuntimeError("transient table error")
        self.deleted.append((partition_key, row_key))
        self.entities = [
            e
            for e in self.entities
            if not (e["PartitionKey"] == partition_key and e["RowKey"] == row_key)
        ]


class TestAzureTablePurge:
    def _store(self, **kwargs) -> AzureTableStore:
        return AzureTableStore(client=FakeDeletingTableClient(**kwargs))

    def test_only_partitions_before_the_cutoff_are_touched(self):
        store = self._store()
        now = datetime.now(UTC)
        store.log(_entry_at(now - timedelta(days=400)))
        store.log(_entry_at(now - timedelta(days=1)))

        removed = store.purge_older_than(365)

        assert removed == 1
        assert len(store._client.entities) == 1

    def test_the_filter_bounds_on_the_partition_not_the_rows(self):
        """Scanning rows to find old ones would read the whole table."""
        store = self._store()
        store.purge_older_than(365)

        used = store._client.filters[-1]
        assert used.startswith("PartitionKey lt '")
        cutoff = used.split("'")[1]
        assert len(cutoff) == 10, "the cutoff is a date, matching the partition key"

    def test_zero_days_does_not_even_query(self):
        store = self._store()
        assert store.purge_older_than(0) == 0
        assert store._client.filters == []

    def test_one_failing_delete_does_not_stop_the_purge(self):
        store = self._store()
        now = datetime.now(UTC)
        for _ in range(3):
            store.log(_entry_at(now - timedelta(days=400)))
        doomed = store._client.entities[1]["RowKey"]
        store._client.fail_on = doomed

        removed = store.purge_older_than(365)

        assert removed == 2, "the other two still go"


class TestPurgeExpiredDriver:
    def test_it_uses_the_configured_store(self, monkeypatch, sqlite_store):
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "30")
        sqlite_store.log(_entry_at(datetime.now(UTC) - timedelta(days=90)))
        audit.set_store(sqlite_store)
        try:
            assert audit.purge_expired() == 1
        finally:
            audit.set_store(None)

    def test_a_broken_store_does_not_raise(self, monkeypatch):
        class Exploding(NullStore):
            def purge_older_than(self, days: int) -> int:
                raise RuntimeError("table unreachable")

        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "30")
        audit.set_store(Exploding())
        try:
            # Housekeeping must never be the reason the server falls over.
            assert audit.purge_expired() == 0
        finally:
            audit.set_store(None)

    def test_disabled_retention_skips_the_store(self, monkeypatch):
        class Tripwire(NullStore):
            def purge_older_than(self, days: int) -> int:
                raise AssertionError("must not be called when retention is disabled")

        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "0")
        audit.set_store(Tripwire())
        try:
            assert audit.purge_expired() == 0
        finally:
            audit.set_store(None)
