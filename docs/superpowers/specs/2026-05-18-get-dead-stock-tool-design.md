# Design: `get_dead_stock` MCP tool

**Status:** Approved — ready for implementation plan
**Date:** 2026-05-18
**Author:** Roland Slagter (via Claude brainstorming)

## Purpose

Add an MCP tool that surfaces products with stock that have not sold for a
configurable number of months and whose stock value exceeds a configurable
threshold. The aim is actionable insight for the CMS Lelystad team:
"these specific SKUs / makes / productgroups have €X of capital tied up in
non-moving inventory."

This complements the existing `get_stock_levels` tool, which only shows the
current snapshot without any sales-recency context.

## Scope (in / out)

**In scope:**
- New pure function `get_dead_stock(...)` in `src/admcms_mcp/tools.py`.
- `@mcp.tool()` wrapper in `src/admcms_mcp/server.py` with descriptive docstring
  and `_audited` instrumentation, matching the pattern of the other 21 tools.
- Bump `tool_count` in the `health_check` tool from 21 to 22.
- Unit tests in `tests/test_tools.py` following the existing mock-based pattern.

**Out of scope (future work, not blockers):**
- A separate "stock turnover ratio" tool (different question, complex math).
- Dashboards or alerts derived from this tool's output.
- Forecasting / predictive flagging.
- Tooling to *act* on dead stock (markdowns, supplier returns).

## Tool signature

```python
def get_dead_stock(
    months_without_sale: int = 12,
    min_stock_value: float = 100.0,
    group_by: str = "product",
    include_inactive_warehouses: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]
```

### Parameters

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `months_without_sale` | int | 12 | Clamped to range 1–60. The cutoff date sent to SQL is `today - months_without_sale`. |
| `min_stock_value` | float | 100.0 | In home currency. Filters `ProductWarehouse.ActualStockValue >= ?`. Negative values clamped to 0. |
| `group_by` | str | `"product"` | One of `"product"`, `"make"`, `"productgroup"`. Any other value returns an error response (no exception raised). |
| `include_inactive_warehouses` | bool | `False` | When `False`, excludes warehouses `OS` (Overstock), `SA` (Samples), `DM` (Damaged). When `True`, all warehouses included. |
| `limit` | int | 100 | Clamped 1–500 via the existing `_MAX_LIMIT` constant. |
| `offset` | int | 0 | Clamped to ≥ 0. Standard paging. |

## Response shape

### For `group_by="product"`

```jsonc
{
  "data": [
    {
      "Productcode": "MAH-12345",
      "Description": "Oil filter MAHLE OC-90",
      "Make": "MAHLE",
      "Productgroup": "HOD",
      "Warehouse": "CM",
      "ActualStock": 47,
      "ActualStockValue": 1245.30,
      "CostPrice": 26.50,
      "last_sale_date": "2024-02-14",
      "days_since_sale": 824,
      "months_since_sale": 27.1
    }
    // ...
  ],
  "summary": {
    "count": 87,
    "total_dead_stock_value": 142350.00,
    "total_dead_sku_count": 412,
    "never_sold_count": 23,
    "never_sold_value": 4120.50,
    "months_without_sale": 12,
    "min_stock_value": 100.0,
    "group_by": "product",
    "include_inactive_warehouses": false,
    "cutoff_date": "2025-05-18",
    "offset": 0,
    "limit": 100,
    "has_more": false,
    "next_offset": null
  },
  "error": null
}
```

### For `group_by="make"` (or `"productgroup"`)

```jsonc
{
  "data": [
    {
      "Make": "MAHLE",
      "dead_sku_count": 38,
      "total_stock_value": 24510.00,
      "avg_months_since_sale": 19.4,
      "oldest_last_sale": "2022-08-03"
    }
  ],
  "summary": { /* same fields as above */ }
}
```

For `group_by="productgroup"` the first column is `Productgroup`; the rest of
the shape is identical.

### Summary-field semantics

- `count`: rows in the **current page** (i.e. `len(data)`).
- `total_dead_stock_value` and `total_dead_sku_count`: totals **across all
  matches**, not just the current page. This is essential — paging would
  otherwise mislead the caller.
- `never_sold_count` / `never_sold_value`: products with stock above the
  threshold that have **no row at all** in `SalesinvoiceLines`. Always
  computed; not included in `data`. This was an explicit design choice — the
  user wanted visibility without polluting the main list.
- `cutoff_date`: the actual date the tool used as "today minus N months",
  expressed as ISO `YYYY-MM-DD`. Aids reproducibility and debugging.

## SQL approach

Approach A from brainstorming: single-query LEFT JOIN with a CTE for last-sale
date. Two DB trips total (one for paged data, one for totals).

### Main data query (group_by="product")

```sql
WITH last_sale AS (
    SELECT sil.Productcode, MAX(si.InvoiceDate) AS LastSaleDate
    FROM SalesinvoiceLines sil
    JOIN Salesinvoice si ON si.Invoicenr = sil.Invoicenr
    WHERE si.InvoiceType = '0'   -- regular invoices only; credit notes excluded
    GROUP BY sil.Productcode
),
stock AS (
    SELECT pw.Productcode, pw.Warehouse, pw.ActualStock,
           pw.ActualStockValue, pw.CostPrice
    FROM ProductWarehouse pw
    WHERE pw.Active = 1
      AND pw.ActualStock > 0
      AND pw.ActualStockValue >= ?              -- min_stock_value
      AND (? = 1 OR pw.Warehouse NOT IN ('OS','SA','DM'))
)
SELECT s.Productcode, p.Description, p.Make, p.Productgroup,
       s.Warehouse, s.ActualStock, s.ActualStockValue, s.CostPrice,
       ls.LastSaleDate,
       DATEDIFF(day, ls.LastSaleDate, GETDATE()) AS days_since_sale
FROM stock s
JOIN Product p     ON p.Productcode = s.Productcode
JOIN last_sale ls  ON ls.Productcode = s.Productcode    -- INNER: never-sold excluded
WHERE ls.LastSaleDate < ?                                -- cutoff_date
ORDER BY s.ActualStockValue DESC
OFFSET ? ROWS FETCH NEXT ? ROWS ONLY;
```

`months_since_sale` is computed in Python from `days_since_sale / 30.44` (avg
days per month), rounded to 1 decimal. Computing in Python avoids `DATEDIFF`
month-boundary quirks.

### Summary query (totals + never-sold counters)

```sql
WITH last_sale AS ( /* same as above */ ),
stock          AS ( /* same as above */ )
SELECT
  SUM(CASE WHEN ls.LastSaleDate < ?    THEN s.ActualStockValue ELSE 0 END) AS total_dead_value,
  COUNT(DISTINCT CASE WHEN ls.LastSaleDate < ? THEN s.Productcode END)     AS total_dead_count,
  SUM(CASE WHEN ls.LastSaleDate IS NULL THEN s.ActualStockValue ELSE 0 END) AS never_sold_value,
  COUNT(DISTINCT CASE WHEN ls.LastSaleDate IS NULL THEN s.Productcode END)  AS never_sold_count
FROM stock s
LEFT JOIN last_sale ls ON ls.Productcode = s.Productcode;
```

### Aggregated query (group_by="make" / "productgroup")

```sql
WITH last_sale AS ( /* same */ ),
stock          AS ( /* same */ )
SELECT
    p.Make,
    COUNT(DISTINCT s.Productcode) AS dead_sku_count,
    SUM(s.ActualStockValue)       AS total_stock_value,
    AVG(CAST(DATEDIFF(day, ls.LastSaleDate, GETDATE()) AS FLOAT) / 30.44) AS avg_months_since_sale,
    MIN(ls.LastSaleDate)          AS oldest_last_sale
FROM stock s
JOIN Product p    ON p.Productcode = s.Productcode
JOIN last_sale ls ON ls.Productcode = s.Productcode
WHERE ls.LastSaleDate < ?
GROUP BY p.Make
ORDER BY total_stock_value DESC
OFFSET ? ROWS FETCH NEXT ? ROWS ONLY;
```

(For `group_by="productgroup"` swap `p.Make` for `p.Productgroup` — substituted
via a safe lookup dict, not string interpolation of user input.)

## Edge cases and decisions

| Case | Decision | Rationale |
|---|---|---|
| Credit notes (`InvoiceType='1'`) | Excluded from `last_sale` | Credit notes are returns; including them would mark recently-returned products as "actively selling" — the opposite of reality. |
| Discontinued products (`Product.InActiveProgram=1`) with stock | **Included** | These are a major source of dead stock; the whole point is to surface them. May be split into a separate flag in a future iteration. |
| `months_without_sale` out of range | Clamped to 1–60 | Same defensive style as `_MAX_LIMIT` clamping. |
| `min_stock_value < 0` | Clamped to 0 | Negative threshold is nonsensical. |
| Unknown `group_by` | Return `{"data": [], "summary": null, "error": "Invalid group_by ..."}` | No exception bubbles to MCP layer; matches existing tool conventions. |
| Never-sold products (no row in SalesinvoiceLines) | Excluded from `data`, counted in `summary.never_sold_*` | Explicit user choice during brainstorming. |
| Performance of `last_sale` CTE (12M-row `SalesinvoiceLines`) | Accept the full scan in v1, measure during manual verification | A datetime pre-filter would conflate "never sold" with "sold long ago", losing the never-sold tracking. If the query exceeds ~5 s, fall back to two-step approach: existence-check query for "ever sold" → second query for "sold in cutoff window". |
| `Salesinvoice.Invoicenr` ↔ `SalesinvoiceLines.Invoicenr` FK | To verify during TDD via `get_schema()` | Other tools already use this join, so it should hold. |

## Testing

Mock-based unit tests in `tests/test_tools.py` following the pattern of
`TestSearchProducts`. No live-DB integration tests (no other tool has them).

Test cases:

- `test_returns_dead_products_per_sku` — happy path, response shape.
- `test_excludes_recently_sold_via_cutoff_date` — verify the `cutoff_date`
  parameter sent to SQL equals today minus `months_without_sale`.
- `test_never_sold_counted_separately` — never-sold products appear in summary
  counters, not in `data`.
- `test_default_excludes_inactive_warehouses` — SQL contains `NOT IN
  ('OS','SA','DM')`; with `include_inactive_warehouses=True` it does not.
- `test_group_by_make_returns_aggregated_shape` — `data` rows have `Make`,
  `dead_sku_count`, `total_stock_value`, etc.; no per-SKU fields.
- `test_group_by_productgroup` — similar.
- `test_invalid_group_by_returns_error` — `group_by="bogus"` returns an error
  response, does not raise.
- `test_clamps_months_without_sale` — `999` → `60`; `0` → `1`.
- `test_clamps_min_stock_value` — negative → 0.
- `test_paging_summary` — `has_more=True`, `next_offset` correct.
- `test_db_error_returns_error_response` — exception in `execute_query_params`
  surfaces as `{"data": [], "error": "..."}`, not raised.

## Manual verification (post-implementation)

1. Start the server locally: `uvicorn admcms_mcp.server:app --reload`.
2. Call `get_dead_stock()` with default args via MCP-inspector or Claude
   Desktop.
3. Cross-check a handful of returned SKUs against manual SQL:
   ```sql
   SELECT MAX(si.InvoiceDate)
   FROM Salesinvoice si JOIN SalesinvoiceLines sil ON si.Invoicenr = sil.Invoicenr
   WHERE sil.Productcode = '...' AND si.InvoiceType = '0';
   ```
4. Measure query duration. If >5 s, schedule the fallback approach from the
   table above.

## Files touched

- `src/admcms_mcp/tools.py` — add `get_dead_stock` pure function + private helper for SQL building.
- `src/admcms_mcp/server.py` — add import, add `@mcp.tool()` wrapper, bump `tool_count` to 22 in `health_check`.
- `tests/test_tools.py` — add `TestGetDeadStock` class with the cases listed above.

No changes to `schema.py`, `db.py`, `auth.py`, `audit.py`, or admin routes.
