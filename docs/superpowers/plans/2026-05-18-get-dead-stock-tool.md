# `get_dead_stock` MCP Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an MCP tool `get_dead_stock` that surfaces products with stock that have not sold for a configurable number of months and exceed a configurable stock-value threshold, with grouping by SKU / make / productgroup.

**Architecture:** Pure function in `tools.py` (single SQL trip for the paged data, second SQL trip for unpaged totals/never-sold counters), `@mcp.tool()` wrapper in `server.py`, mock-based unit tests in `tests/test_tools.py`. Follows the established pattern of the other 21 tools in this project.

**Tech Stack:** Python 3.11+, pyodbc against SQL Server (ADMCMS), `mcp` library's `FastMCP` for tool registration, pytest with `unittest.mock` for tests.

**Spec:** [`docs/superpowers/specs/2026-05-18-get-dead-stock-tool-design.md`](../specs/2026-05-18-get-dead-stock-tool-design.md)

---

## File structure

- **Modify** `src/admcms_mcp/query_helpers.py` — add `subtract_months(iso, months)` helper for cutoff-date math.
- **Modify** `src/admcms_mcp/tools.py` — add `get_dead_stock` pure function plus two private SQL-building helpers (`_dead_stock_main_sql_per_product`, `_dead_stock_main_sql_aggregated`) and one shared CTE helper (`_dead_stock_ctes`).
- **Modify** `src/admcms_mcp/server.py` — add import, add `@mcp.tool()` wrapper, bump `tool_count` in `health_check` from 21 to 22.
- **Modify** `tests/test_query_helpers.py` — add tests for `subtract_months`.
- **Modify** `tests/test_tools.py` — add `TestGetDeadStock` class.

No new files. No changes to `db.py`, `auth.py`, `audit.py`, `schema.py`, admin routes.

---

## Task 1: Add `subtract_months` helper to `query_helpers.py`

**Files:**
- Modify: `src/admcms_mcp/query_helpers.py`
- Test: `tests/test_query_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_query_helpers.py`:

```python
from admcms_mcp.query_helpers import subtract_months


class TestSubtractMonths:
    def test_simple_month_subtraction(self):
        assert subtract_months("2026-05-18", 1) == "2026-04-18"

    def test_crosses_year_boundary(self):
        assert subtract_months("2026-02-18", 3) == "2025-11-18"

    def test_handles_end_of_month_when_target_is_shorter(self):
        # March 31 minus 1 month → Feb 28 (not Feb 31)
        assert subtract_months("2026-03-31", 1) == "2026-02-28"

    def test_handles_leap_year(self):
        # Feb 29 2024 minus 12 months → Feb 28 2023
        assert subtract_months("2024-02-29", 12) == "2023-02-28"

    def test_zero_months_returns_same_date(self):
        assert subtract_months("2026-05-18", 0) == "2026-05-18"

    def test_raises_on_invalid_iso(self):
        import pytest
        with pytest.raises(ValueError):
            subtract_months("not-a-date", 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_query_helpers.py::TestSubtractMonths -v`
Expected: FAIL with `ImportError: cannot import name 'subtract_months'`.

- [ ] **Step 3: Implement `subtract_months`**

Append to `src/admcms_mcp/query_helpers.py`:

```python
import calendar


def subtract_months(iso: str, months: int) -> str:
    """Subtract N months from an ISO date, clamping the day to the target month's length.

    >>> subtract_months("2026-05-18", 1)
    '2026-04-18'
    >>> subtract_months("2026-03-31", 1)
    '2026-02-28'

    Raises ValueError for any input that is not exactly YYYY-MM-DD.
    """
    d = date.fromisoformat(iso)
    month = d.month - int(months)
    year = d.year
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()
```

(`date` is already imported at the top of the file; `calendar` is a new stdlib import.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_query_helpers.py::TestSubtractMonths -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/admcms_mcp/query_helpers.py tests/test_query_helpers.py
git commit -m "feat(query_helpers): add subtract_months helper for cutoff-date math"
```

---

## Task 2: Add `get_dead_stock` skeleton with input validation

This task establishes the function shape, parameter validation, and clamping. SQL execution is stubbed; later tasks add the queries.

**Files:**
- Modify: `src/admcms_mcp/tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:

```python
from unittest.mock import patch

from admcms_mcp.tools import get_dead_stock


class TestGetDeadStockValidation:
    def test_invalid_group_by_returns_error_response(self):
        result = get_dead_stock(group_by="bogus")
        assert result["data"] == []
        assert result["error"] is not None
        assert "group_by" in result["error"]

    @patch("admcms_mcp.tools.execute_query_params")
    def test_clamps_months_without_sale_high(self, mock_execute):
        mock_execute.side_effect = [[{"total_dead_value": 0, "total_dead_count": 0,
                                        "never_sold_value": 0, "never_sold_count": 0}], []]
        result = get_dead_stock(months_without_sale=999)
        assert result["summary"]["months_without_sale"] == 60

    @patch("admcms_mcp.tools.execute_query_params")
    def test_clamps_months_without_sale_low(self, mock_execute):
        mock_execute.side_effect = [[{"total_dead_value": 0, "total_dead_count": 0,
                                        "never_sold_value": 0, "never_sold_count": 0}], []]
        result = get_dead_stock(months_without_sale=0)
        assert result["summary"]["months_without_sale"] == 1

    @patch("admcms_mcp.tools.execute_query_params")
    def test_clamps_negative_min_stock_value(self, mock_execute):
        mock_execute.side_effect = [[{"total_dead_value": 0, "total_dead_count": 0,
                                        "never_sold_value": 0, "never_sold_count": 0}], []]
        result = get_dead_stock(min_stock_value=-50.0)
        assert result["summary"]["min_stock_value"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py::TestGetDeadStockValidation -v`
Expected: FAIL with `ImportError: cannot import name 'get_dead_stock'`.

- [ ] **Step 3: Implement the skeleton**

Add to `src/admcms_mcp/tools.py`. First update the imports at the top:

```python
from admcms_mcp.query_helpers import (
    admcms_date,
    format_response,
    paging_clause,
    paging_summary,
    sanitize_like,
    shift_year,
    subtract_months,
)
```

Then add at the end of the file:

```python
_DEAD_STOCK_GROUP_BY = ("product", "make", "productgroup")
_DEAD_STOCK_INACTIVE_WAREHOUSES = ("OS", "SA", "DM")
_DEAD_STOCK_MIN_MONTHS = 1
_DEAD_STOCK_MAX_MONTHS = 60


def get_dead_stock(
    months_without_sale: int = 12,
    min_stock_value: float = 100.0,
    group_by: str = "product",
    include_inactive_warehouses: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Surface products with stock that have not sold in the last N months."""
    if group_by not in _DEAD_STOCK_GROUP_BY:
        return format_response(
            data=[],
            error=(
                f"Invalid group_by: {group_by!r}; "
                f"must be one of {list(_DEAD_STOCK_GROUP_BY)}"
            ),
        )

    try:
        effective_months = max(
            _DEAD_STOCK_MIN_MONTHS,
            min(int(months_without_sale), _DEAD_STOCK_MAX_MONTHS),
        )
        effective_min_value = max(0.0, float(min_stock_value))
        effective_limit = max(1, min(int(limit), _MAX_LIMIT))
        effective_offset = max(0, int(offset))

        today_iso = datetime.date.today().isoformat()
        cutoff_date = subtract_months(today_iso, effective_months)

        # SQL execution wired up in Task 3.
        rows: list[dict[str, Any]] = []
        totals = {
            "total_dead_value": 0.0,
            "total_dead_count": 0,
            "never_sold_value": 0.0,
            "never_sold_count": 0,
        }

        summary = {
            "count": len(rows),
            "total_dead_stock_value": float(totals["total_dead_value"] or 0),
            "total_dead_sku_count": int(totals["total_dead_count"] or 0),
            "never_sold_count": int(totals["never_sold_count"] or 0),
            "never_sold_value": float(totals["never_sold_value"] or 0),
            "months_without_sale": effective_months,
            "min_stock_value": effective_min_value,
            "group_by": group_by,
            "include_inactive_warehouses": include_inactive_warehouses,
            "cutoff_date": cutoff_date,
            **paging_summary(effective_offset, effective_limit, len(rows)),
        }
        return format_response(data=rows, summary=summary)
    except ValueError as e:
        logger.info("get_dead_stock rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_dead_stock failed")
        return format_response(data=[], error=f"Execution error: {e}")
```

Note: the validation tests in Step 1 mock `execute_query_params` even though the skeleton does not call it yet. The mocks become load-bearing in Task 3; setting them up now means the same tests keep passing across both tasks.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py::TestGetDeadStockValidation -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/admcms_mcp/tools.py tests/test_tools.py
git commit -m "feat(tools): add get_dead_stock skeleton with input validation"
```

---

## Task 3: Implement per-product happy path (data + summary SQL)

This task wires up the two SQL queries: one for paged dead-stock rows, one for unpaged totals + never-sold counters. After this task the tool returns real data for `group_by="product"`.

**Files:**
- Modify: `src/admcms_mcp/tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:

```python
class TestGetDeadStockPerProduct:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_dead_products_with_expected_shape(self, mock_execute):
        # Two calls: 1) summary query, 2) paged data query
        mock_execute.side_effect = [
            [{"total_dead_value": 5000.0, "total_dead_count": 12,
              "never_sold_value": 250.0, "never_sold_count": 3}],
            [{
                "Productcode": "MAH-001",
                "Description": "Oil filter",
                "Make": "MAHLE",
                "Productgroup": "HOD",
                "Warehouse": "CM",
                "ActualStock": 47,
                "ActualStockValue": 1245.30,
                "CostPrice": 26.50,
                "LastSaleDate": "2024-02-14",
                "days_since_sale": 824,
            }],
        ]
        result = get_dead_stock(months_without_sale=12, min_stock_value=100.0)
        assert result["error"] is None
        assert len(result["data"]) == 1
        row = result["data"][0]
        assert row["Productcode"] == "MAH-001"
        assert row["last_sale_date"] == "2024-02-14"
        assert row["days_since_sale"] == 824
        assert row["months_since_sale"] == 27.1
        assert result["summary"]["total_dead_stock_value"] == 5000.0
        assert result["summary"]["total_dead_sku_count"] == 12
        assert result["summary"]["never_sold_count"] == 3
        assert result["summary"]["never_sold_value"] == 250.0
        assert result["summary"]["count"] == 1

    @patch("admcms_mcp.tools.execute_query_params")
    def test_default_excludes_inactive_warehouses(self, mock_execute):
        mock_execute.side_effect = [
            [{"total_dead_value": 0, "total_dead_count": 0,
              "never_sold_value": 0, "never_sold_count": 0}],
            [],
        ]
        get_dead_stock()
        # Second call (the paged data SQL) should contain the warehouse filter
        paged_sql = mock_execute.call_args_list[1][0][0]
        assert "Warehouse NOT IN" in paged_sql
        assert "'OS'" in paged_sql and "'SA'" in paged_sql and "'DM'" in paged_sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_include_inactive_warehouses_omits_filter(self, mock_execute):
        mock_execute.side_effect = [
            [{"total_dead_value": 0, "total_dead_count": 0,
              "never_sold_value": 0, "never_sold_count": 0}],
            [],
        ]
        get_dead_stock(include_inactive_warehouses=True)
        paged_sql = mock_execute.call_args_list[1][0][0]
        assert "Warehouse NOT IN" not in paged_sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_paging_summary_reflects_full_page(self, mock_execute):
        mock_execute.side_effect = [
            [{"total_dead_value": 0, "total_dead_count": 0,
              "never_sold_value": 0, "never_sold_count": 0}],
            [{"Productcode": f"P{i}", "Description": "x", "Make": "x",
              "Productgroup": "x", "Warehouse": "CM", "ActualStock": 1,
              "ActualStockValue": 1.0, "CostPrice": 1.0,
              "LastSaleDate": "2024-01-01", "days_since_sale": 800}
             for i in range(5)],
        ]
        result = get_dead_stock(limit=5)
        assert result["summary"]["has_more"] is True
        assert result["summary"]["next_offset"] == 5

    @patch("admcms_mcp.tools.execute_query_params")
    def test_cutoff_date_is_today_minus_months(self, mock_execute):
        import datetime as _dt
        from admcms_mcp.query_helpers import subtract_months
        mock_execute.side_effect = [
            [{"total_dead_value": 0, "total_dead_count": 0,
              "never_sold_value": 0, "never_sold_count": 0}],
            [],
        ]
        result = get_dead_stock(months_without_sale=6)
        expected_cutoff = subtract_months(_dt.date.today().isoformat(), 6)
        assert result["summary"]["cutoff_date"] == expected_cutoff
        # And the same value should have been sent to SQL as a param
        # The cutoff_date param is shared across both queries; pick either call.
        paged_params = mock_execute.call_args_list[1][0][1]
        assert expected_cutoff in paged_params
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py::TestGetDeadStockPerProduct -v`
Expected: FAIL — the skeleton returns empty data and zero totals.

- [ ] **Step 3: Add the SQL helpers and wire them into `get_dead_stock`**

Above the `get_dead_stock` function in `src/admcms_mcp/tools.py`, add:

```python
def _dead_stock_ctes(include_inactive_warehouses: bool) -> tuple[str, list[str]]:
    """Build the shared CTE block + the warehouse-filter params placeholder.

    Returns (cte_sql, []) — no params from the CTE itself; the stock CTE's
    placeholders are bound by the outer query (min_stock_value first, then
    the warehouse-include flag).
    """
    warehouse_clause = (
        ""
        if include_inactive_warehouses
        else " AND pw.Warehouse NOT IN ('OS','SA','DM')"
    )
    cte_sql = (
        "WITH last_sale AS ("
        "  SELECT sil.Productcode, MAX(si.InvoiceDate) AS LastSaleDate "
        "  FROM SalesinvoiceLines sil "
        "  JOIN Salesinvoice si ON si.Invoicenr = sil.Invoicenr "
        "  WHERE si.InvoiceType = '0' "
        "  GROUP BY sil.Productcode"
        "), "
        "stock AS ("
        "  SELECT pw.Productcode, pw.Warehouse, pw.ActualStock, "
        "         pw.ActualStockValue, pw.CostPrice "
        "  FROM ProductWarehouse pw "
        "  WHERE pw.Active = 1 "
        "    AND pw.ActualStock > 0 "
        "    AND pw.ActualStockValue >= ?"
        f"   {warehouse_clause}"
        ") "
    )
    return cte_sql, []


def _dead_stock_summary_sql(include_inactive_warehouses: bool) -> str:
    cte_sql, _ = _dead_stock_ctes(include_inactive_warehouses)
    return (
        cte_sql
        + "SELECT "
        "  SUM(CASE WHEN ls.LastSaleDate < ? THEN s.ActualStockValue ELSE 0 END) "
        "    AS total_dead_value, "
        "  COUNT(DISTINCT CASE WHEN ls.LastSaleDate < ? THEN s.Productcode END) "
        "    AS total_dead_count, "
        "  SUM(CASE WHEN ls.LastSaleDate IS NULL THEN s.ActualStockValue ELSE 0 END) "
        "    AS never_sold_value, "
        "  COUNT(DISTINCT CASE WHEN ls.LastSaleDate IS NULL THEN s.Productcode END) "
        "    AS never_sold_count "
        "FROM stock s "
        "LEFT JOIN last_sale ls ON ls.Productcode = s.Productcode"
    )


def _dead_stock_main_sql_per_product(include_inactive_warehouses: bool) -> str:
    cte_sql, _ = _dead_stock_ctes(include_inactive_warehouses)
    return (
        cte_sql
        + "SELECT s.Productcode, p.Description, p.Make, p.Productgroup, "
        "       s.Warehouse, s.ActualStock, s.ActualStockValue, s.CostPrice, "
        "       ls.LastSaleDate, "
        "       DATEDIFF(day, ls.LastSaleDate, GETDATE()) AS days_since_sale "
        "FROM stock s "
        "JOIN Product p ON p.Productcode = s.Productcode "
        "JOIN last_sale ls ON ls.Productcode = s.Productcode "
        "WHERE ls.LastSaleDate < ? "
        "ORDER BY s.ActualStockValue DESC"
    )
```

Now replace the `# SQL execution wired up in Task 3.` block in `get_dead_stock` with:

```python
        summary_sql = _dead_stock_summary_sql(include_inactive_warehouses)
        # Params: min_stock_value (stock CTE), cutoff (×2 in CASE WHEN)
        summary_params = (effective_min_value, cutoff_date, cutoff_date)
        totals_rows = execute_query_params(summary_sql, summary_params, max_rows=1)
        totals = totals_rows[0] if totals_rows else {
            "total_dead_value": 0, "total_dead_count": 0,
            "never_sold_value": 0, "never_sold_count": 0,
        }

        if group_by == "product":
            main_sql_base = _dead_stock_main_sql_per_product(include_inactive_warehouses)
        else:
            # Wired up in Task 4.
            main_sql_base = _dead_stock_main_sql_per_product(include_inactive_warehouses)

        page_sql, page_params = paging_clause(effective_offset, effective_limit)
        main_sql = main_sql_base + page_sql
        # Params: min_stock_value, cutoff, offset, limit
        main_params = (effective_min_value, cutoff_date, *page_params)
        raw_rows = execute_query_params(main_sql, main_params, max_rows=effective_limit)
        rows = _convert_rows([_shape_dead_stock_row(r) for r in raw_rows])
```

Also add this row-shaping helper near the other private helpers near the top of the file (after `_convert_rows`):

```python
def _shape_dead_stock_row(row: dict[str, Any]) -> dict[str, Any]:
    """Rename LastSaleDate → last_sale_date and compute months_since_sale."""
    out = dict(row)
    last = out.pop("LastSaleDate", None)
    days = out.get("days_since_sale")
    out["last_sale_date"] = last
    out["months_since_sale"] = (
        round(float(days) / 30.44, 1) if days is not None else None
    )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py::TestGetDeadStockPerProduct tests/test_tools.py::TestGetDeadStockValidation -v`
Expected: 9 passed (4 from Task 2 still green + 5 new).

- [ ] **Step 5: Commit**

```bash
git add src/admcms_mcp/tools.py tests/test_tools.py
git commit -m "feat(tools): implement get_dead_stock per-product data + summary queries"
```

---

## Task 4: Implement aggregated mode (`group_by="make"` / `"productgroup"`)

**Files:**
- Modify: `src/admcms_mcp/tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:

```python
class TestGetDeadStockAggregated:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_group_by_make_returns_aggregated_shape(self, mock_execute):
        mock_execute.side_effect = [
            [{"total_dead_value": 100000.0, "total_dead_count": 250,
              "never_sold_value": 5000.0, "never_sold_count": 20}],
            [
                {"Make": "MAHLE", "dead_sku_count": 38,
                 "total_stock_value": 24510.0, "avg_months_since_sale": 19.4,
                 "oldest_last_sale": "2022-08-03"},
                {"Make": "BOSCH", "dead_sku_count": 22,
                 "total_stock_value": 14200.0, "avg_months_since_sale": 14.1,
                 "oldest_last_sale": "2023-01-15"},
            ],
        ]
        result = get_dead_stock(group_by="make")
        assert result["error"] is None
        assert len(result["data"]) == 2
        row = result["data"][0]
        assert row["Make"] == "MAHLE"
        assert row["dead_sku_count"] == 38
        assert row["total_stock_value"] == 24510.0
        # No per-SKU fields in aggregated mode
        assert "Productcode" not in row
        assert "Warehouse" not in row
        # Summary still has the per-product totals
        assert result["summary"]["total_dead_sku_count"] == 250

    @patch("admcms_mcp.tools.execute_query_params")
    def test_group_by_productgroup_uses_productgroup_column(self, mock_execute):
        mock_execute.side_effect = [
            [{"total_dead_value": 0, "total_dead_count": 0,
              "never_sold_value": 0, "never_sold_count": 0}],
            [{"Productgroup": "HOD", "dead_sku_count": 10,
              "total_stock_value": 500.0, "avg_months_since_sale": 16.0,
              "oldest_last_sale": "2023-05-01"}],
        ]
        result = get_dead_stock(group_by="productgroup")
        assert result["data"][0]["Productgroup"] == "HOD"
        # And the SQL should GROUP BY p.Productgroup, not p.Make
        paged_sql = mock_execute.call_args_list[1][0][0]
        assert "GROUP BY p.Productgroup" in paged_sql
        assert "p.Make" not in paged_sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py::TestGetDeadStockAggregated -v`
Expected: FAIL — the temporary Task 3 fallback returns per-product shape for these group_by values.

- [ ] **Step 3: Add the aggregated SQL builder and wire it in**

Add to `src/admcms_mcp/tools.py` near the other dead-stock helpers:

```python
_DEAD_STOCK_GROUP_COLUMN = {
    "make": "p.Make",
    "productgroup": "p.Productgroup",
}


def _dead_stock_main_sql_aggregated(
    group_by: str, include_inactive_warehouses: bool
) -> str:
    """SQL for group_by='make' or 'productgroup'.

    `group_by` MUST already be validated against _DEAD_STOCK_GROUP_COLUMN —
    we look up the column name from a static dict, never interpolate raw
    user input.
    """
    group_column = _DEAD_STOCK_GROUP_COLUMN[group_by]
    cte_sql, _ = _dead_stock_ctes(include_inactive_warehouses)
    return (
        cte_sql
        + f"SELECT {group_column}, "
        "       COUNT(DISTINCT s.Productcode) AS dead_sku_count, "
        "       SUM(s.ActualStockValue) AS total_stock_value, "
        "       AVG(CAST(DATEDIFF(day, ls.LastSaleDate, GETDATE()) AS FLOAT) / 30.44) "
        "         AS avg_months_since_sale, "
        "       MIN(ls.LastSaleDate) AS oldest_last_sale "
        "FROM stock s "
        "JOIN Product p ON p.Productcode = s.Productcode "
        "JOIN last_sale ls ON ls.Productcode = s.Productcode "
        "WHERE ls.LastSaleDate < ? "
        f"GROUP BY {group_column} "
        "ORDER BY total_stock_value DESC"
    )
```

Replace the `# Wired up in Task 4.` else-branch in `get_dead_stock`:

```python
        if group_by == "product":
            main_sql_base = _dead_stock_main_sql_per_product(include_inactive_warehouses)
            shape_fn = _shape_dead_stock_row
        else:
            main_sql_base = _dead_stock_main_sql_aggregated(
                group_by, include_inactive_warehouses
            )
            shape_fn = _shape_dead_stock_aggregated_row
```

And replace the `rows = _convert_rows([_shape_dead_stock_row(r) for r in raw_rows])` line with:

```python
        rows = _convert_rows([shape_fn(r) for r in raw_rows])
```

Then add the aggregated shaper:

```python
def _shape_dead_stock_aggregated_row(row: dict[str, Any]) -> dict[str, Any]:
    """Aggregated rows already have the right keys; just round avg_months."""
    out = dict(row)
    avg = out.get("avg_months_since_sale")
    if avg is not None:
        out["avg_months_since_sale"] = round(float(avg), 1)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py::TestGetDeadStockAggregated -v`
Expected: 2 passed.

Also re-run the full new-test set to make sure nothing regressed:

Run: `pytest tests/test_tools.py -k "DeadStock" -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/admcms_mcp/tools.py tests/test_tools.py
git commit -m "feat(tools): implement get_dead_stock aggregated mode (make/productgroup)"
```

---

## Task 5: DB-error handling test

The existing try/except in the skeleton already handles `Exception` by returning an error response. This task just adds the missing test to lock the behavior down.

**Files:**
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:

```python
class TestGetDeadStockErrorHandling:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_db_exception_returns_error_response(self, mock_execute):
        mock_execute.side_effect = Exception("DB down")
        result = get_dead_stock()
        assert result["data"] == []
        assert result["error"] is not None
        assert "DB down" in result["error"]
```

- [ ] **Step 2: Run test to verify it passes (already)**

Run: `pytest tests/test_tools.py::TestGetDeadStockErrorHandling -v`
Expected: 1 passed (the skeleton's exception handler covers this).

If it fails, fix the exception handler in `get_dead_stock` — but verify first; the wrapping `try/except Exception` from Task 2 should already produce `{"data": [], "error": "Execution error: DB down"}`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_tools.py
git commit -m "test(tools): cover get_dead_stock DB-error handling"
```

---

## Task 6: Register tool in `server.py` + bump `tool_count`

**Files:**
- Modify: `src/admcms_mcp/server.py`

- [ ] **Step 1: Add the import**

In `src/admcms_mcp/server.py`, add to the alphabetically-sorted import block at the top (between `get_customer_orders` and `get_discount_analysis`):

```python
from admcms_mcp.tools import get_dead_stock as _get_dead_stock
```

- [ ] **Step 2: Add the `@mcp.tool()` wrapper inside `_build_mcp_server`**

Add the following wrapper just before the `health_check` wrapper near the end of `_build_mcp_server` (`health_check` should remain the last tool registered):

```python
    @mcp.tool()
    def get_dead_stock(
        months_without_sale: int = 12,
        min_stock_value: float = 100.0,
        group_by: str = "product",
        include_inactive_warehouses: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Surface products with stock that have not sold in the last N months.

        Returns the SKUs (or makes/productgroups) that are tying up capital in
        non-moving inventory. Use this when a customer asks "what's stuck on
        the shelves?" or for periodic dead-stock reviews.

        Definition of "dead":
          - ProductWarehouse.ActualStock > 0
          - ProductWarehouse.ActualStockValue >= min_stock_value (default 100)
          - No invoice line (InvoiceType='0') in the last `months_without_sale`
            months (default 12).

        Never-sold products (no row at all in SalesinvoiceLines) are excluded
        from the main `data` list but tracked separately in
        `summary.never_sold_count` and `summary.never_sold_value` so they
        aren't lost.

        By default warehouses 'OS' (Overstock), 'SA' (Samples), and 'DM'
        (Damaged) are excluded — set `include_inactive_warehouses=True` to
        include them.

        Args:
            months_without_sale: months threshold, clamped 1-60. Default 12.
            min_stock_value: home-currency floor on ActualStockValue,
                clamped to >= 0. Default 100.0.
            group_by: "product" (default, per-SKU per-warehouse rows),
                "make", or "productgroup".
            include_inactive_warehouses: include OS/SA/DM. Default False.
            limit: rows per page, clamped 1-500, default 100.
            offset: row to start from, default 0.

        Returns: {data, summary: {total_dead_stock_value, total_dead_sku_count,
                  never_sold_count, never_sold_value, cutoff_date, ...}, error}.
        """
        return _audited(
            "get_dead_stock",
            {
                "months_without_sale": months_without_sale,
                "min_stock_value": min_stock_value,
                "group_by": group_by,
                "include_inactive_warehouses": include_inactive_warehouses,
                "limit": limit,
                "offset": offset,
            },
            lambda: _get_dead_stock(
                months_without_sale=months_without_sale,
                min_stock_value=min_stock_value,
                group_by=group_by,
                include_inactive_warehouses=include_inactive_warehouses,
                limit=limit,
                offset=offset,
            ),
        )
```

- [ ] **Step 3: Bump `tool_count` in `health_check`**

In `src/admcms_mcp/server.py` find the `health_check` tool (around line 899) and change:

```python
"tool_count": 21,
```

to:

```python
"tool_count": 22,
```

- [ ] **Step 4: Verify the server starts and the tool is registered**

Run:
```bash
pytest tests/test_server_mcp.py -v
```
Expected: all existing server tests still pass. If there's a test that enumerates registered tool names, expect 22 now instead of 21 — adjust the test if needed.

If no such test exists, do a quick import sanity check:
```bash
python -c "from admcms_mcp.server import app; print('ok')"
```
Expected: prints `ok`, no exceptions.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/admcms_mcp/server.py
git commit -m "feat(tools): register get_dead_stock MCP tool + bump health_check count"
```

---

## Task 7: Manual verification

This task is exploratory — no checkboxes for the SQL cross-check itself, only for the final acceptance.

- [ ] **Step 1: Start the server locally**

In one terminal:
```bash
uvicorn admcms_mcp.server:app --reload
```
Expected: server starts on port 8000, logs `AUTH_DISABLED is true` (if running with auth disabled for local dev).

- [ ] **Step 2: Call the tool**

Use either the MCP inspector, Claude Desktop, or a quick curl-equivalent. Smallest call:
```
get_dead_stock(months_without_sale=12, min_stock_value=100, group_by="product", limit=5)
```
Note the `cutoff_date` in the summary, and pick 2-3 returned Productcodes for cross-check.

- [ ] **Step 3: Cross-check against manual SQL**

For each picked Productcode, run via the existing `query_database` tool or directly against ADMCMS:
```sql
SELECT MAX(si.InvoiceDate) AS last_sale
FROM Salesinvoice si
JOIN SalesinvoiceLines sil ON si.Invoicenr = sil.Invoicenr
WHERE sil.Productcode = '<code>' AND si.InvoiceType = '0';
```
Confirm the manual `last_sale` matches the `last_sale_date` returned by the tool, and that it is earlier than the `cutoff_date`.

- [ ] **Step 4: Measure query duration**

Note the time taken for the dead-stock call (the response includes no timing, so use a stopwatch or the server log). If the call takes more than 5 seconds, file a follow-up to optimize per the spec's fallback plan (two-step "ever sold" + "sold recently" approach).

- [ ] **Step 5: Test the aggregated modes**

```
get_dead_stock(group_by="make")
get_dead_stock(group_by="productgroup")
```
Sanity-check the totals roughly match the per-product run (`SUM(total_stock_value)` should be in the same ballpark as `total_dead_stock_value`).

- [ ] **Step 6: Mark done**

If all manual checks pass, the feature is complete. No code change in this task; only validation.

---

## Self-review notes

- All five sections of the spec (purpose, signature, response shape, SQL approach, edge cases, testing) are covered by Tasks 1-6.
- The `subtract_months` helper from Task 1 is consumed by Task 2's skeleton.
- The `_shape_dead_stock_row` function defined in Task 3 is consumed by Task 4 (alongside the new `_shape_dead_stock_aggregated_row`).
- The `tool_count` bump in Task 6 matches the +1 tool registration in the same task.
- The never-sold counters in the spec are wired through the summary query in Task 3 and asserted in Task 3's `test_returns_dead_products_with_expected_shape`. The spec said never-sold should be "always tracked, regardless of group_by" — Task 4's `test_group_by_make_returns_aggregated_shape` re-asserts that the summary fields persist when the data shape changes.
- No placeholders, no "TODO", no "similar to Task N" without code.
