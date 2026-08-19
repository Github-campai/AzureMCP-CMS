"""AzureTableStore: the audit trail that survives the nightly restart.

Driven by a fake table client, so these run without Azure. What they pin down is
the contract with Table Storage: entity shape, the filters, the ordering trick,
and the size caps that would otherwise fail a write and lose the entry.
"""

import re
from datetime import UTC, datetime, timedelta

import pytest

from admcms_mcp.audit import AuditEntry, SqlCall
from admcms_mcp.audit_stores import AzureTableStore, build_store_from_env


class FakeTableClient:
    """Records upserts and replays them, filter-aware enough for these tests."""

    def __init__(self):
        self.entities: list[dict] = []
        self.filters: list[str | None] = []
        self.selects: list[list[str] | None] = []

    def upsert_entity(self, entity):
        self.entities.append(dict(entity))

    def query_entities(self, query_filter=None, select=None):
        self.filters.append(query_filter)
        self.selects.append(select)
        # Newest first, which is what the inverted RowKey buys in real Table Storage.
        rows = sorted(self.entities, key=lambda e: e["RowKey"])
        # Only `entry_id eq '...'` is honoured -- the one filter get() depends on.
        # The rest is asserted on the emitted OData string instead.
        match = re.search(r"entry_id eq '([^']*)'", query_filter or "")
        if match:
            rows = [r for r in rows if r.get("entry_id") == match.group(1)]
        return rows


@pytest.fixture
def store():
    return AzureTableStore(client=FakeTableClient())


def _entry(**kwargs) -> AuditEntry:
    defaults = {
        "tool_name": "get_revenue",
        "args": {"start": "2026-01-01"},
        "duration_ms": 42,
        "row_count": 7,
        "user": "iemand@campai.nl",
    }
    return AuditEntry(**{**defaults, **kwargs})


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_entity_carries_everything_the_admin_endpoints_need(store):
    entry = _entry(sql_queries=[SqlCall(sql="SELECT 1", duration_ms=5, row_count=1)])
    store.log(entry)

    written = store._client.entities[0]
    assert written["entry_id"] == entry.id
    assert written["tool_name"] == "get_revenue"
    assert written["duration_ms"] == 42
    assert written["row_count"] == 7
    assert written["user_id"] == "iemand@campai.nl"
    assert "SELECT 1" in written["sql_queries_json"]
    assert written["has_error"] is False


def test_partition_is_the_date_so_time_queries_stay_cheap(store):
    store.log(_entry(timestamp="2026-08-19T07:15:00.000Z"))
    assert store._client.entities[0]["PartitionKey"] == "2026-08-19"


def test_newest_entry_sorts_first(store):
    """Table Storage sorts ascending by RowKey only; the key is inverted for this."""
    store.log(_entry(timestamp="2026-08-19T07:00:00.000Z", tool_name="oudste"))
    store.log(_entry(timestamp="2026-08-19T09:00:00.000Z", tool_name="nieuwste"))

    rows = store.query()
    assert [r["tool_name"] for r in rows] == ["nieuwste", "oudste"]


def test_error_sets_the_boolean_used_for_filtering(store):
    store.log(_entry(error="ProgrammingError: boem"))
    written = store._client.entities[0]
    assert written["has_error"] is True
    assert "boem" in written["error"]


def test_oversized_payload_is_truncated_instead_of_failing_the_write(store):
    """A property over 64 KiB is rejected by Table Storage; losing the entry is worse."""
    store.log(_entry(args={"sql": "x" * 100_000}))
    written = store._client.entities[0]
    assert len(written["args_json"]) <= AzureTableStore.MAX_PROPERTY_CHARS
    assert "afgekapt" in written["args_json"]


def test_a_malformed_timestamp_does_not_break_the_write(store):
    store.log(_entry(timestamp="niet-een-datum"))
    assert len(store._client.entities) == 1


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_query_respects_the_limit(store):
    for i in range(10):
        store.log(_entry(tool_name=f"tool{i}"))
    assert len(store.query(limit=3)) == 3


def test_query_limit_is_capped(store):
    store.log(_entry())
    store.query(limit=10_000_000)  # must not explode
    assert AzureTableStore.MAX_QUERY_LIMIT == 1000


def test_filters_are_translated_to_odata(store):
    since = datetime(2026, 8, 1, tzinfo=UTC)
    store.query(tool="get_revenue", since=since, errors_only=True)
    used = store._client.filters[-1]
    assert "tool_name eq 'get_revenue'" in used
    assert "has_error eq true" in used
    # Partition bound as well as timestamp: without it this scans every partition.
    assert "PartitionKey ge '2026-08-01'" in used
    assert "timestamp ge '2026-08-01" in used


def test_a_quote_in_the_tool_name_cannot_break_the_filter(store):
    store.query(tool="get'revenue")
    assert "get''revenue" in store._client.filters[-1]


def test_no_filter_when_nothing_is_asked(store):
    store.query()
    assert store._client.filters[-1] is None


def test_get_expands_the_json_columns(store):
    entry = _entry(
        args={"start": "2026-01-01"},
        sql_queries=[SqlCall(sql="SELECT 1")],
        result_summary={"total": 5},
    )
    store.log(entry)

    got = store.get(entry.id)
    assert got["args"] == {"start": "2026-01-01"}
    assert got["sql_queries"][0]["sql"] == "SELECT 1"
    assert got["result_summary"] == {"total": 5}


def test_get_returns_none_for_an_unknown_id(store):
    store.log(_entry())
    assert store.get("bestaat-niet") is None


def test_get_hands_back_text_when_the_payload_was_truncated(store):
    """Truncated JSON no longer parses; the caller should still see the content."""
    entry = _entry(args={"sql": "y" * 100_000})
    store.log(entry)
    got = store.get(entry.id)
    assert isinstance(got["args"], str)
    assert "afgekapt" in got["args"]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_aggregates_per_tool(store):
    store.log(_entry(tool_name="get_revenue", duration_ms=100))
    store.log(_entry(tool_name="get_revenue", duration_ms=200))
    store.log(_entry(tool_name="get_top_customers", duration_ms=50, error="stuk"))

    stats = store.stats()
    assert stats["total_calls"] == 3
    assert stats["total_errors"] == 1
    assert round(stats["error_rate_pct"]) == 33

    per_tool = {t["tool_name"]: t for t in stats["per_tool"]}
    assert per_tool["get_revenue"]["calls"] == 2
    assert per_tool["get_revenue"]["avg_duration_ms"] == 150
    assert per_tool["get_top_customers"]["errors"] == 1
    # Busiest tool first, same as the SQLite store.
    assert stats["per_tool"][0]["tool_name"] == "get_revenue"


def test_stats_defaults_to_the_last_seven_days(store):
    stats = store.stats()
    since = datetime.fromisoformat(stats["since"].replace("Z", "+00:00"))
    assert timedelta(days=6) < (datetime.now(UTC) - since) < timedelta(days=8)


def test_stats_on_an_empty_table_does_not_divide_by_zero(store):
    stats = store.stats()
    assert stats["total_calls"] == 0
    assert stats["error_rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_env_selects_the_azure_store(monkeypatch):
    monkeypatch.setenv("AUDIT_STORE", "azure_table")
    monkeypatch.setenv("AUDIT_TABLE_ACCOUNT_URL", "https://acct.table.core.windows.net")

    created = {}

    class FakeService:
        def __init__(self, endpoint=None, credential=None):
            created["endpoint"] = endpoint

        def create_table_if_not_exists(self, name):
            created["table"] = name

        def get_table_client(self, name):
            return FakeTableClient()

    import azure.data.tables as tables

    monkeypatch.setattr(tables, "TableServiceClient", FakeService)
    store = build_store_from_env()

    assert isinstance(store, AzureTableStore)
    assert created["endpoint"] == "https://acct.table.core.windows.net"
    assert created["table"] == "admcmsaudit"


def test_azure_store_refuses_to_start_without_a_target(monkeypatch):
    monkeypatch.setenv("AUDIT_STORE", "azure_table")
    monkeypatch.delenv("AUDIT_TABLE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("AUDIT_AZURE_CONNECTION_STRING", raising=False)

    with pytest.raises(ValueError, match="AUDIT_TABLE_ACCOUNT_URL"):
        build_store_from_env()
