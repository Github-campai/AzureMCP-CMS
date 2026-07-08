"""MCP tool handlers.

Pure functions: each takes primitive inputs, returns a JSON-serializable
dict. Error responses use {"error": "..."} so MCP clients (Claude) can
relay the problem to the user without the call failing at protocol level.
"""

import datetime
import logging
import threading
import time
from typing import Any

from admcms_mcp.db import (
    execute_query,
    execute_query_params,
    get_distinct_values,
    get_schema_columns,
)
from admcms_mcp.query_helpers import (
    admcms_date,
    build_filter_where,
    format_response,
    paging_clause,
    paging_summary,
    sanitize_like,
    shift_year,
    subtract_months,
)

logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    """Convert DB values that aren't JSON-serializable (dates, decimals, etc.)."""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if hasattr(value, "__float__") and not isinstance(value, (int, float, bool)):
        return float(value)
    return value


def _convert_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _jsonable(v) for k, v in row.items()} for row in rows]


def query_database(sql: str, max_rows: int = 100) -> dict[str, Any]:
    """Execute a read-only SQL query against ADMCMS and return rows."""
    try:
        rows = execute_query(sql, max_rows=max_rows)
        return {
            "sql": sql,
            "row_count": len(rows),
            "rows": _convert_rows(rows),
        }
    except ValueError as e:
        logger.info("Query rejected: %s", e)
        return {"sql": sql, "error": str(e), "rows": []}
    except Exception as e:
        logger.exception("Query execution failed")
        return {"sql": sql, "error": f"Execution error: {e}", "rows": []}


def get_schema(table_name: str) -> dict[str, Any]:
    """Return column metadata for an ADMCMS table."""
    try:
        columns = get_schema_columns(table_name)
        if not columns:
            return {"table": table_name, "error": "Table not found or no columns", "columns": []}
        return {"table": table_name, "columns": columns}
    except ValueError as e:
        return {"table": table_name, "error": str(e), "columns": []}
    except Exception as e:
        logger.exception("get_schema failed")
        return {"table": table_name, "error": f"Execution error: {e}", "columns": []}


def get_lookups(table_name: str, column_name: str, top_n: int = 50) -> dict[str, Any]:
    """Return up to `top_n` distinct values for a column in a table."""
    try:
        values = get_distinct_values(table_name, column_name, top_n=top_n)
        return {
            "table": table_name,
            "column": column_name,
            "values": [_jsonable(v) for v in values],
        }
    except ValueError as e:
        return {"table": table_name, "column": column_name, "error": str(e), "values": []}
    except Exception as e:
        logger.exception("get_lookups failed")
        return {
            "table": table_name,
            "column": column_name,
            "error": f"Execution error: {e}",
            "values": [],
        }


_MAX_LIMIT = 500


def search_products(search_term: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Search ADMCMS products by Productcode or Searchname.

    Uses LIKE matching with user input escaped to prevent unintended
    wildcard matching. Results ordered active-first then by price desc.
    Paging via `offset`; `summary.has_more` + `summary.next_offset`
    tell callers whether/where to fetch the next page.
    """
    try:
        effective_limit = max(1, min(int(limit), _MAX_LIMIT))
        effective_offset = max(0, int(offset))
        pattern = f"%{sanitize_like(search_term)}%"
        page_sql, page_params = paging_clause(effective_offset, effective_limit)
        sql = (
            "SELECT Productcode, Searchname, Make, Productgroup, Salesprice, "
            "InActiveProgram "
            "FROM PRODUCT "
            "WHERE (Productcode LIKE ? OR Searchname LIKE ?) "
            "ORDER BY InActiveProgram ASC, Salesprice DESC" + page_sql
        )
        rows = execute_query_params(sql, (pattern, pattern, *page_params), max_rows=effective_limit)
        summary = {
            "count": len(rows),
            "search_term": search_term,
            **paging_summary(effective_offset, effective_limit, len(rows)),
        }
        return format_response(data=rows, summary=summary)
    except ValueError as e:
        logger.info("search_products rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("search_products failed")
        return format_response(data=[], error=f"Execution error: {e}")


_VALID_GROUP_BY = ("total", "month", "make", "customer", "country", "productgroup")

# Per group_by: the column in the row that identifies the group
_GROUP_KEY_FIELD = {
    "month": "period",
    "make": "Make",
    "customer": "Searchname",
    "country": "DeliveryCountry",
    "productgroup": "Productgroup",
}


def _fetch_revenue(
    start_date: str, end_date: str, group_by: str, filters: dict | None = None
) -> list[dict[str, Any]]:
    """Run the revenue SQL for a given date range + group_by. No LY merging.

    `filters` (optional, additive): country works on every group_by
    (Salesinvoice.DeliveryCountry, header-level); customer_group/business_unit
    add a CUSTSUPPLIER join when not already present; make/productgroup only
    apply when group_by is itself "make"/"productgroup" (the only variants
    that join PRODUCT). Unsupported dim/group_by combinations are ignored.
    """
    start_c8 = admcms_date(start_date)
    end_c8 = admcms_date(end_date)
    base_where = "WHERE InvoiceDate >= ? AND InvoiceDate < ?"
    params = (start_c8, end_c8)
    needs_cs = _wants_cs(filters)

    if group_by == "total":
        from_clause = "FROM SALESINVOICE "
        if needs_cs:
            from_clause += "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = Customer "
        filter_sql, filter_params = build_filter_where(
            filters, cs="cs" if needs_cs else None, country_col="DeliveryCountry"
        )
        sql = (
            "SELECT SUM(LineTotal) AS total_amount, "
            "COUNT(*) AS invoice_count "
            + from_clause + base_where + filter_sql
        )
        rows = execute_query_params(sql, params + filter_params, max_rows=1)
        return rows or [{"total_amount": 0, "invoice_count": 0}]

    if group_by == "month":
        from_clause = "FROM SALESINVOICE "
        if needs_cs:
            from_clause += "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = Customer "
        filter_sql, filter_params = build_filter_where(
            filters, cs="cs" if needs_cs else None, country_col="DeliveryCountry"
        )
        sql = (
            "SELECT SUBSTRING(InvoiceDate, 1, 6) AS period, "
            "SUM(LineTotal) AS amount "
            + from_clause + base_where + filter_sql + " "
            "GROUP BY SUBSTRING(InvoiceDate, 1, 6) "
            "ORDER BY period"
        )
        params = params + filter_params
    elif group_by == "country":
        from_clause = "FROM SALESINVOICE "
        if needs_cs:
            from_clause += "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = Customer "
        filter_sql, filter_params = build_filter_where(
            filters, cs="cs" if needs_cs else None, country_col="DeliveryCountry"
        )
        sql = (
            "SELECT DeliveryCountry, SUM(LineTotal) AS amount "
            + from_clause + base_where + filter_sql + " "
            "GROUP BY DeliveryCountry ORDER BY amount DESC"
        )
        params = params + filter_params
    elif group_by == "make":
        join_clause = "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer " if needs_cs else ""
        filter_sql, filter_params = build_filter_where(
            filters, cs="cs" if needs_cs else None, p="p", country_col="si.DeliveryCountry"
        )
        sql = (
            "SELECT p.Make, SUM(sil.InvoiceAmountHC) AS amount "
            "FROM SALESINVOICELINES sil "
            "JOIN SALESINVOICE si ON si.Invoicenr = sil.Invoicenr "
            "JOIN PRODUCT p ON p.Productcode = sil.Productcode "
            + join_clause
            + base_where.replace("InvoiceDate", "si.InvoiceDate")
            + filter_sql
            + " "
            "GROUP BY p.Make ORDER BY amount DESC"
        )
        params = params + filter_params
    elif group_by == "productgroup":
        join_clause = "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer " if needs_cs else ""
        filter_sql, filter_params = build_filter_where(
            filters, cs="cs" if needs_cs else None, p="p", country_col="si.DeliveryCountry"
        )
        sql = (
            "SELECT p.Productgroup, SUM(sil.InvoiceAmountHC) AS amount "
            "FROM SALESINVOICELINES sil "
            "JOIN SALESINVOICE si ON si.Invoicenr = sil.Invoicenr "
            "JOIN PRODUCT p ON p.Productcode = sil.Productcode "
            + join_clause
            + base_where.replace("InvoiceDate", "si.InvoiceDate")
            + filter_sql
            + " "
            "GROUP BY p.Productgroup ORDER BY amount DESC"
        )
        params = params + filter_params
    else:  # customer
        filter_sql, filter_params = build_filter_where(
            filters, cs="cs", country_col="si.DeliveryCountry"
        )
        sql = (
            "SELECT cs.Searchname, SUM(si.LineTotal) AS amount "
            "FROM SALESINVOICE si "
            "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer "
            + base_where.replace("InvoiceDate", "si.InvoiceDate")
            + filter_sql
            + " "
            "GROUP BY cs.Searchname ORDER BY amount DESC"
        )
        params = params + filter_params
    return execute_query_params(sql, params, max_rows=500)


def _index_ly_rows(ly_rows: list[dict], group_by: str) -> dict[Any, dict]:
    """Index LY rows by their group key. For `month`, shift year so LY
    202503 maps to current 202603, enabling 1-on-1 match."""
    key_field = _GROUP_KEY_FIELD[group_by]
    indexed: dict[Any, dict] = {}
    for r in ly_rows:
        key = r.get(key_field)
        if group_by == "month" and isinstance(key, str) and len(key) == 6:
            key = str(int(key[:4]) + 1) + key[4:]
        indexed[key] = r
    return indexed


def _pct(part: float, whole: float) -> float | None:
    return (part / whole * 100) if whole else None


def _wants_cs(filters: dict | None) -> bool:
    """True when `filters` asks for a dim that requires a CUSTSUPPLIER join."""
    return bool(filters and (filters.get("customer_group") or filters.get("business_unit")))


def _wants_p(filters: dict | None) -> bool:
    """True when `filters` asks for a dim that requires a PRODUCT join."""
    return bool(filters and (filters.get("make") or filters.get("productgroup")))


def get_revenue(
    start_date: str,
    end_date: str,
    group_by: str = "total",
    include_ly: bool = False,
    filters: dict | None = None,
) -> dict[str, Any]:
    """Aggregate sales invoice revenue between two dates.

    `group_by`: total, month, make, customer, country, productgroup.
    `include_ly`: when True, compute same period exactly 1 year earlier and
    add `amount_ly`, `delta`, `delta_pct` to each row + LY totals to summary.

    Revenue uses SALESINVOICE.LineTotal (ex-VAT, home currency) for header-
    level groupings (total, month, country, customer). For per-make and
    per-productgroup breakdowns, SALESINVOICELINES.InvoiceAmountHC is used
    because LineTotal is not on the line level.

    `filters` (optional dict, additive — omitting it leaves behaviour
    unchanged): `country` applies on every group_by; `customer_group` /
    `business_unit` apply on every group_by (adds a CUSTSUPPLIER join when
    needed); `make` / `productgroup` only apply when group_by is itself
    "make" or "productgroup" (the only variants that join PRODUCT).
    """
    try:
        if group_by not in _VALID_GROUP_BY:
            return format_response(
                data=[],
                error=(
                    f"Invalid group_by {group_by!r}; must be one of: {', '.join(_VALID_GROUP_BY)}"
                ),
            )

        current = _fetch_revenue(start_date, end_date, group_by, filters=filters)

        # --- include_ly = False: classic behaviour ---
        if not include_ly:
            if group_by == "total":
                row = current[0]
                return format_response(
                    data=[row],
                    summary={
                        "total_amount": row.get("total_amount") or 0,
                        "invoice_count": row.get("invoice_count") or 0,
                        "period": f"{start_date}..{end_date}",
                        "group_by": "total",
                    },
                )
            total = sum(float(r.get("amount") or 0) for r in current)
            return format_response(
                data=current,
                summary={
                    "total_amount": total,
                    "row_count": len(current),
                    "period": f"{start_date}..{end_date}",
                    "group_by": group_by,
                },
            )

        # --- include_ly = True: fetch LY and merge ---
        ly_start = shift_year(start_date, -1)
        ly_end = shift_year(end_date, -1)
        ly_rows = _fetch_revenue(ly_start, ly_end, group_by, filters=filters)

        if group_by == "total":
            cur_row = current[0]
            ly_row = ly_rows[0]
            cur_amount = float(cur_row.get("total_amount") or 0)
            ly_amount = float(ly_row.get("total_amount") or 0)
            delta = cur_amount - ly_amount
            merged = {
                **cur_row,
                "total_amount_ly": ly_amount,
                "invoice_count_ly": ly_row.get("invoice_count") or 0,
                "delta_amount": delta,
                "delta_pct": _pct(delta, ly_amount),
            }
            return format_response(
                data=[merged],
                summary={
                    "total_amount": cur_amount,
                    "total_amount_ly": ly_amount,
                    "delta_amount": delta,
                    "delta_pct": _pct(delta, ly_amount),
                    "period": f"{start_date}..{end_date}",
                    "ly_period": f"{ly_start}..{ly_end}",
                    "group_by": "total",
                },
            )

        # Non-total: merge per-group-key
        key_field = _GROUP_KEY_FIELD[group_by]
        ly_by_key = _index_ly_rows(ly_rows, group_by)
        merged_rows: list[dict] = []
        keys_seen: set = set()
        for cur in current:
            key = cur.get(key_field)
            keys_seen.add(key)
            ly_match = ly_by_key.get(key)
            ly_amount = float(ly_match.get("amount") or 0) if ly_match else 0.0
            cur_amount = float(cur.get("amount") or 0)
            delta = cur_amount - ly_amount
            merged_rows.append(
                {**cur, "amount_ly": ly_amount, "delta": delta, "delta_pct": _pct(delta, ly_amount)}
            )
        # Groups that existed LY but not in current period
        for key, ly_match in ly_by_key.items():
            if key in keys_seen:
                continue
            ly_amount = float(ly_match.get("amount") or 0)
            merged_rows.append(
                {
                    key_field: key,
                    "amount": 0.0,
                    "amount_ly": ly_amount,
                    "delta": -ly_amount,
                    "delta_pct": -100.0 if ly_amount else None,
                }
            )

        total_cur = sum(float(r.get("amount") or 0) for r in current)
        total_ly = sum(float(r.get("amount") or 0) for r in ly_rows)
        summary_delta = total_cur - total_ly
        return format_response(
            data=merged_rows,
            summary={
                "total_amount": total_cur,
                "total_amount_ly": total_ly,
                "delta_amount": summary_delta,
                "delta_pct": _pct(summary_delta, total_ly),
                "row_count": len(merged_rows),
                "period": f"{start_date}..{end_date}",
                "ly_period": f"{ly_start}..{ly_end}",
                "group_by": group_by,
            },
        )
    except ValueError as e:
        logger.info("get_revenue rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_revenue failed")
        return format_response(data=[], error=f"Execution error: {e}")


_VALID_MARGIN_GROUP_BY = (
    "total",
    "country",
    "make",
    "productgroup",
    "customer",
    "segment",
)

_MARGIN_SEGMENT_CASE = (
    "CASE "
    "WHEN cs.CustSuppGroup = 'ND' THEN 'B2C' "
    "WHEN cs.CustSuppGroup IN ('G2', 'G3', 'G4', 'G5', 'G6') THEN 'B2B' "
    "WHEN cs.CustSuppGroup = 'INTC' THEN 'IC' "
    "ELSE 'Overig' END"
)


def _fetch_margin(
    start_date: str, end_date: str, group_by: str, filters: dict | None = None
) -> list[dict[str, Any]]:
    """Line-level turnover + cost + margin, grouped as requested.

    `filters` (optional, additive): every group_by variant shares the same
    `sil`/`si` base FROM, so country (si.DeliveryCountry) always applies;
    customer_group/business_unit add a CUSTSUPPLIER join when the "customer"/
    "segment" group_by hasn't already added one; make/productgroup add a
    PRODUCT join when the "make"/"productgroup" group_by hasn't already
    added one.
    """
    start_c8 = admcms_date(start_date)
    end_c8 = admcms_date(end_date)
    where = "WHERE si.InvoiceDate >= ? AND si.InvoiceDate < ?"
    params = (start_c8, end_c8)

    base_from = "FROM SalesinvoiceLines sil JOIN Salesinvoice si ON si.Invoicenr = sil.Invoicenr "
    amounts = "SUM(sil.InvoiceAmountHC) AS turnover, SUM(sil.CostAmountBookedHC) AS cost"

    has_cs = group_by in ("customer", "segment")
    has_p = group_by in ("make", "productgroup")
    needs_cs = _wants_cs(filters) and not has_cs
    needs_p = _wants_p(filters) and not has_p

    extra_join = ""
    if needs_cs:
        extra_join += "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer "
    if needs_p:
        extra_join += "JOIN PRODUCT p ON p.Productcode = sil.Productcode "

    filter_sql, filter_params = build_filter_where(
        filters,
        cs="cs" if (has_cs or needs_cs) else None,
        p="p" if (has_p or needs_p) else None,
        country_col="si.DeliveryCountry",
    )
    params = params + filter_params

    if group_by == "total":
        sql = f"SELECT {amounts} {base_from}{extra_join}{where}{filter_sql}"
        return execute_query_params(sql, params, max_rows=1) or [{"turnover": 0, "cost": 0}]

    if group_by == "country":
        sql = (
            f"SELECT si.DeliveryCountry, {amounts} "
            f"{base_from}{extra_join}{where}{filter_sql} "
            "GROUP BY si.DeliveryCountry ORDER BY turnover DESC"
        )
    elif group_by == "make":
        sql = (
            f"SELECT p.Make, {amounts} "
            f"{base_from}JOIN PRODUCT p ON p.Productcode = sil.Productcode {extra_join}"
            f"{where}{filter_sql} "
            "GROUP BY p.Make ORDER BY turnover DESC"
        )
    elif group_by == "productgroup":
        sql = (
            f"SELECT p.Productgroup, {amounts} "
            f"{base_from}JOIN PRODUCT p ON p.Productcode = sil.Productcode {extra_join}"
            f"{where}{filter_sql} "
            "GROUP BY p.Productgroup ORDER BY turnover DESC"
        )
    elif group_by == "customer":
        sql = (
            f"SELECT cs.Searchname, {amounts} "
            f"{base_from}JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer {extra_join}"
            f"{where}{filter_sql} "
            "GROUP BY cs.Searchname ORDER BY turnover DESC"
        )
    else:  # segment
        sql = (
            f"SELECT {_MARGIN_SEGMENT_CASE} AS business_unit, {amounts} "
            f"{base_from}JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer {extra_join}"
            f"{where}{filter_sql} "
            f"GROUP BY {_MARGIN_SEGMENT_CASE} ORDER BY turnover DESC"
        )
    return execute_query_params(sql, params, max_rows=500)


_MARGIN_KEY_FIELD = {
    "country": "DeliveryCountry",
    "make": "Make",
    "productgroup": "Productgroup",
    "customer": "Searchname",
    "segment": "business_unit",
}


def _enrich_margin_row(row: dict) -> dict:
    turnover = float(row.get("turnover") or 0)
    cost = float(row.get("cost") or 0)
    margin = turnover - cost
    return {
        **row,
        "margin": margin,
        "margin_pct": _pct(margin, turnover),
    }


def get_margin_report(
    start_date: str,
    end_date: str,
    group_by: str = "total",
    include_ly: bool = False,
    filters: dict | None = None,
) -> dict[str, Any]:
    """Gross-margin analysis on SalesinvoiceLines.

    Formula: margin = SUM(InvoiceAmountHC) - SUM(CostAmountBookedHC).
    Margin % = margin / turnover * 100.

    `group_by`: total, country, make, productgroup, customer, segment.
    `include_ly`: same period 1 year earlier, delta added per row.

    `filters` (optional dict, additive): supports customer_group,
    business_unit, country, make, productgroup on every group_by — the
    query always joins SalesinvoiceLines+Salesinvoice, and adds a
    CUSTSUPPLIER/PRODUCT join on demand when the chosen group_by hasn't
    already added one.

    NOTE: this is line-level gross margin (sale - cost of goods). It does
    not deduct shipping, VAT, order-handling costs or returns. If your
    dashboard's "margin" definition differs, numbers will diverge — verify
    against a known data point.
    """
    try:
        if group_by not in _VALID_MARGIN_GROUP_BY:
            return format_response(
                data=[],
                error=(
                    f"Invalid group_by {group_by!r}; must be one of: "
                    f"{', '.join(_VALID_MARGIN_GROUP_BY)}"
                ),
            )
        # Validate dates up-front so we don't reach the SQL layer with bad input.
        admcms_date(start_date)
        admcms_date(end_date)

        current_raw = _fetch_margin(start_date, end_date, group_by, filters=filters)
        current = [_enrich_margin_row(r) for r in current_raw]

        if not include_ly:
            if group_by == "total":
                row = current[0]
                return format_response(
                    data=[row],
                    summary={
                        "turnover": row["turnover"] or 0,
                        "cost": row["cost"] or 0,
                        "margin": row["margin"],
                        "margin_pct": row["margin_pct"],
                        "period": f"{start_date}..{end_date}",
                        "group_by": "total",
                    },
                )
            t_sum = sum(float(r.get("turnover") or 0) for r in current)
            c_sum = sum(float(r.get("cost") or 0) for r in current)
            return format_response(
                data=current,
                summary={
                    "turnover": t_sum,
                    "cost": c_sum,
                    "margin": t_sum - c_sum,
                    "margin_pct": _pct(t_sum - c_sum, t_sum),
                    "row_count": len(current),
                    "period": f"{start_date}..{end_date}",
                    "group_by": group_by,
                },
            )

        # include_ly
        ly_start = shift_year(start_date, -1)
        ly_end = shift_year(end_date, -1)
        ly_raw = _fetch_margin(ly_start, ly_end, group_by, filters=filters)
        ly = [_enrich_margin_row(r) for r in ly_raw]

        if group_by == "total":
            cur_row = current[0]
            ly_row = ly[0]
            return format_response(
                data=[
                    {
                        **cur_row,
                        "turnover_ly": ly_row["turnover"] or 0,
                        "cost_ly": ly_row["cost"] or 0,
                        "margin_ly": ly_row["margin"],
                        "margin_pct_ly": ly_row["margin_pct"],
                        "delta_turnover": cur_row["turnover"] - (ly_row["turnover"] or 0),
                        "delta_margin": cur_row["margin"] - (ly_row["margin"] or 0),
                    }
                ],
                summary={
                    "turnover": cur_row["turnover"],
                    "turnover_ly": ly_row["turnover"] or 0,
                    "margin": cur_row["margin"],
                    "margin_ly": ly_row["margin"] or 0,
                    "margin_pct": cur_row["margin_pct"],
                    "margin_pct_ly": ly_row["margin_pct"],
                    "period": f"{start_date}..{end_date}",
                    "ly_period": f"{ly_start}..{ly_end}",
                    "group_by": "total",
                },
            )

        key_field = _MARGIN_KEY_FIELD[group_by]
        ly_by_key = {r.get(key_field): r for r in ly}
        merged: list[dict] = []
        seen = set()
        for cur_row in current:
            k = cur_row.get(key_field)
            seen.add(k)
            ly_match = ly_by_key.get(k)
            ly_turnover = float(ly_match.get("turnover") or 0) if ly_match else 0.0
            ly_margin = float(ly_match.get("margin") or 0) if ly_match else 0.0
            merged.append(
                {
                    **cur_row,
                    "turnover_ly": ly_turnover,
                    "margin_ly": ly_margin,
                    "margin_pct_ly": ly_match.get("margin_pct") if ly_match else None,
                    "delta_turnover": cur_row["turnover"] - ly_turnover,
                    "delta_margin": cur_row["margin"] - ly_margin,
                }
            )
        for k, ly_only in ly_by_key.items():
            if k in seen:
                continue
            merged.append(
                {
                    key_field: k,
                    "turnover": 0.0,
                    "cost": 0.0,
                    "margin": 0.0,
                    "margin_pct": None,
                    "turnover_ly": float(ly_only.get("turnover") or 0),
                    "margin_ly": float(ly_only.get("margin") or 0),
                    "margin_pct_ly": ly_only.get("margin_pct"),
                    "delta_turnover": -float(ly_only.get("turnover") or 0),
                    "delta_margin": -float(ly_only.get("margin") or 0),
                }
            )
        t_cur = sum(float(r.get("turnover") or 0) for r in current)
        t_ly = sum(float(r.get("turnover") or 0) for r in ly)
        m_cur = sum(float(r.get("margin") or 0) for r in current)
        m_ly = sum(float(r.get("margin") or 0) for r in ly)
        return format_response(
            data=merged,
            summary={
                "turnover": t_cur,
                "turnover_ly": t_ly,
                "margin": m_cur,
                "margin_ly": m_ly,
                "margin_pct": _pct(m_cur, t_cur),
                "margin_pct_ly": _pct(m_ly, t_ly),
                "delta_margin": m_cur - m_ly,
                "row_count": len(merged),
                "period": f"{start_date}..{end_date}",
                "ly_period": f"{ly_start}..{ly_end}",
                "group_by": group_by,
            },
        )
    except ValueError as e:
        logger.info("get_margin_report rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_margin_report failed")
        return format_response(data=[], error=f"Execution error: {e}")


def _fetch_kpi_block(
    start_date: str, end_date: str, filters: dict | None = None
) -> dict[str, Any]:
    """Run 2 aggregate queries over Salesinvoice + SalesinvoiceLines.

    Returns a single dict with all headline KPI values.

    `filters` (optional, additive): country and customer_group/business_unit
    apply to both underlying queries (a CUSTSUPPLIER join is added on demand).
    make/productgroup are not supported (no PRODUCT join available here).
    """
    start_c8 = admcms_date(start_date)
    end_c8 = admcms_date(end_date)
    params = (start_c8, end_c8)
    needs_cs = _wants_cs(filters)

    header_join = "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = Customer " if needs_cs else ""
    header_filter_sql, header_filter_params = build_filter_where(
        filters, cs="cs" if needs_cs else None, country_col="DeliveryCountry"
    )
    header_sql = (
        "SELECT "
        "SUM(LineTotal) AS turnover, "
        "SUM(LineTotal + ShipCost) AS turnover_incl_shipping, "
        "COUNT(DISTINCT Invoicenr) AS invoice_count, "
        "COUNT(DISTINCT Customer) AS ordering_customers, "
        "SUM(ShipCost) AS shipping_revenue, "
        "SUM(ShipCostCalculated) AS shipping_cost "
        "FROM Salesinvoice "
        f"{header_join}"
        "WHERE InvoiceDate >= ? AND InvoiceDate < ?" + header_filter_sql
    )
    header_rows = execute_query_params(
        header_sql, params + header_filter_params, max_rows=1
    )
    h = header_rows[0] if header_rows else {}

    lines_join = "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = inv.Customer " if needs_cs else ""
    lines_filter_sql, lines_filter_params = build_filter_where(
        filters, cs="cs" if needs_cs else None, country_col="inv.DeliveryCountry"
    )
    lines_sql = (
        "SELECT COUNT(*) AS line_count, SUM(ThisIinvoice) AS item_count "
        "FROM SalesinvoiceLines sil "
        "JOIN Salesinvoice inv ON inv.Invoicenr = sil.Invoicenr "
        f"{lines_join}"
        "WHERE inv.InvoiceDate >= ? AND inv.InvoiceDate < ?" + lines_filter_sql
    )
    line_rows = execute_query_params(
        lines_sql, params + lines_filter_params, max_rows=1
    )
    ln = line_rows[0] if line_rows else {}

    turnover = float(h.get("turnover") or 0)
    invoice_count = int(h.get("invoice_count") or 0)
    shipping_rev = float(h.get("shipping_revenue") or 0)
    shipping_cost = float(h.get("shipping_cost") or 0)
    shipping_margin = shipping_rev - shipping_cost
    return {
        "turnover": turnover,
        "turnover_incl_shipping": float(h.get("turnover_incl_shipping") or 0),
        "invoice_count": invoice_count,
        "ordering_customers": int(h.get("ordering_customers") or 0),
        "aov": (turnover / invoice_count) if invoice_count else 0.0,
        "shipping_revenue": shipping_rev,
        "shipping_cost": shipping_cost,
        "shipping_margin": shipping_margin,
        "shipping_margin_pct": _pct(shipping_margin, shipping_rev),
        "line_count": int(ln.get("line_count") or 0),
        "item_count": int(ln.get("item_count") or 0),
    }


def get_kpi_summary(
    start_date: str,
    end_date: str,
    include_ly: bool = True,
    filters: dict | None = None,
) -> dict[str, Any]:
    """Top-level performance KPIs (turnover, shipping, customers, AOV, lines).

    Replicates the "KPI cards" row of a typical management dashboard.
    With `include_ly=True` (default), the same period one year earlier is
    queried and delta + delta_pct computed for each metric.

    All amounts are in home currency (HC). Turnover uses
    SALESINVOICE.LineTotal (ex-VAT). AOV = turnover / invoice_count.

    `filters` (optional dict, additive): supports `country` and
    `customer_group`/`business_unit` (adds a CUSTSUPPLIER join). `make`/
    `productgroup` are not supported by this tool (header + line-count
    queries have no PRODUCT join) and are ignored if passed.
    """
    try:
        current = _fetch_kpi_block(start_date, end_date, filters=filters)
        if not include_ly:
            return format_response(
                data=[current],
                summary={
                    **current,
                    "period": f"{start_date}..{end_date}",
                },
            )

        ly_start = shift_year(start_date, -1)
        ly_end = shift_year(end_date, -1)
        ly = _fetch_kpi_block(ly_start, ly_end, filters=filters)

        deltas: dict[str, Any] = {}
        for metric in (
            "turnover",
            "turnover_incl_shipping",
            "invoice_count",
            "ordering_customers",
            "aov",
            "shipping_revenue",
            "shipping_cost",
            "shipping_margin",
            "line_count",
            "item_count",
        ):
            c_val = current.get(metric) or 0
            l_val = ly.get(metric) or 0
            deltas[f"{metric}_ly"] = l_val
            deltas[f"{metric}_delta"] = c_val - l_val
            deltas[f"{metric}_delta_pct"] = _pct(c_val - l_val, l_val)

        data_row = {**current, **deltas}
        return format_response(
            data=[data_row],
            summary={
                **data_row,
                "period": f"{start_date}..{end_date}",
                "ly_period": f"{ly_start}..{ly_end}",
            },
        )
    except ValueError as e:
        logger.info("get_kpi_summary rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_kpi_summary failed")
        return format_response(data=[], error=f"Execution error: {e}")


_BUSINESS_UNIT_CASE = (
    "CASE "
    "WHEN cs.CustSuppGroup = 'ND' THEN 'B2C' "
    "WHEN cs.CustSuppGroup IN ('G2', 'G3', 'G4', 'G5', 'G6') THEN 'B2B' "
    "WHEN cs.CustSuppGroup = 'INTC' THEN 'IC' "
    "ELSE 'Overig' "
    "END"
)


def get_revenue_by_business_unit(start_date: str, end_date: str) -> dict[str, Any]:
    """Aggregate sales invoice revenue between two dates, split by business unit.

    Business units are derived from CustSupplier.CustSuppGroup:
      - 'ND'  -> B2C (consumenten)
      - 'G2' t/m 'G6' -> B2B (zakelijke klanten)
      - 'INTC' -> IC (intercompany)
      - alles anders -> Overig

    Revenue uses SALESINVOICE.LineTotal (ex-VAT, home currency). Dates are
    ISO YYYY-MM-DD; internally converted to ADMCMS char(8).
    """
    try:
        start_c8 = admcms_date(start_date)
        end_c8 = admcms_date(end_date)

        sql = (
            f"SELECT {_BUSINESS_UNIT_CASE} AS business_unit, "
            "COUNT(*) AS invoice_count, "
            "SUM(si.LineTotal) AS total_amount "
            "FROM SALESINVOICE si "
            "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer "
            "WHERE si.InvoiceDate >= ? AND si.InvoiceDate < ? "
            f"GROUP BY {_BUSINESS_UNIT_CASE} "
            "ORDER BY total_amount DESC"
        )
        rows = execute_query_params(sql, (start_c8, end_c8), max_rows=10)
        total = sum(float(r.get("total_amount") or 0) for r in rows)
        invoice_total = sum(int(r.get("invoice_count") or 0) for r in rows)
        return format_response(
            data=rows,
            summary={
                "total_amount": total,
                "invoice_count": invoice_total,
                "period": f"{start_date}..{end_date}",
                "business_units": len(rows),
            },
        )
    except ValueError as e:
        logger.info("get_revenue_by_business_unit rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_revenue_by_business_unit failed")
        return format_response(data=[], error=f"Execution error: {e}")


def get_top_customers(
    start_date: str, end_date: str, limit: int = 10, filters: dict | None = None
) -> dict[str, Any]:
    """Return the top customers on invoice revenue between two dates.

    Filters to CustSuppType = '1' (customers only, not suppliers).
    Revenue uses SALESINVOICE.LineTotal (ex-VAT, home currency). Dates
    are ISO YYYY-MM-DD; internally converted to ADMCMS char(8).

    `filters` (optional dict, additive): supports `country` (si.DeliveryCountry)
    and `customer_group`/`business_unit` (CUSTSUPPLIER is already joined as
    `cs`). `make`/`productgroup` are not supported (no PRODUCT join).
    """
    try:
        effective_limit = max(1, min(int(limit), _MAX_LIMIT))
        start_c8 = admcms_date(start_date)
        end_c8 = admcms_date(end_date)
        filter_sql, filter_params = build_filter_where(
            filters, cs="cs", country_col="si.DeliveryCountry"
        )
        sql = (
            f"SELECT TOP ({effective_limit}) cs.CustSuppnr, cs.Searchname, "
            "cs.CustSuppGroup, "
            "COUNT(DISTINCT si.Invoicenr) AS invoice_count, "
            "SUM(si.LineTotal) AS total_amount "
            "FROM SALESINVOICE si "
            "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer "
            "WHERE si.InvoiceDate >= ? AND si.InvoiceDate < ? "
            "AND cs.CustSuppType = '1'" + filter_sql + " "
            "GROUP BY cs.CustSuppnr, cs.Searchname, cs.CustSuppGroup "
            "ORDER BY total_amount DESC"
        )
        rows = execute_query_params(
            sql, (start_c8, end_c8) + filter_params, max_rows=effective_limit
        )
        total = sum(float(r.get("total_amount") or 0) for r in rows)
        return format_response(
            data=rows,
            summary={
                "top_total_amount": total,
                "count": len(rows),
                "period": f"{start_date}..{end_date}",
            },
        )
    except ValueError as e:
        logger.info("get_top_customers rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_top_customers failed")
        return format_response(data=[], error=f"Execution error: {e}")


def _shipping_scope_clauses(exclude_credits: bool, paid_only: bool) -> str:
    """Extra WHERE fragments for financial-scope filters (empty when False)."""
    parts = []
    if exclude_credits:
        parts.append(" AND InvoiceType = '0'")
    if paid_only:
        parts.append(" AND FullyPaid = 1")
    return "".join(parts)


def get_shipping_summary(
    start_date: str,
    end_date: str,
    exclude_credits: bool = True,
    paid_only: bool = True,
) -> dict[str, Any]:
    """Aggregate shipping metrics over invoices between two dates.

    Returns totals for:
      - ship_cost: what was charged to customer (SALESINVOICE.ShipCost)
      - ship_cost_calculated: the system-calculated cost baseline
        (SALESINVOICE.ShipCostCalculated) — this is essentially what the
        carrier charges us; delta vs. ship_cost = discount or override
      - order_costs: additional order handling fees (SALESINVOICE.OrderCosts)

    Default scope matches financial-reporting convention: only regular
    invoices (InvoiceType='0') that are FullyPaid=1. Flip the flags for a
    broader operational view including credits or unpaid invoices.
    """
    try:
        start_c8 = admcms_date(start_date)
        end_c8 = admcms_date(end_date)
        scope = _shipping_scope_clauses(exclude_credits, paid_only)
        sql = (
            "SELECT COUNT(*) AS invoice_count, "
            "SUM(ShipCost) AS total_ship_cost, "
            "SUM(ShipCostCalculated) AS total_ship_cost_calculated, "
            "SUM(OrderCosts) AS total_order_costs, "
            "AVG(ShipCost) AS avg_ship_cost "
            "FROM SALESINVOICE "
            "WHERE InvoiceDate >= ? AND InvoiceDate < ?" + scope
        )
        rows = execute_query_params(sql, (start_c8, end_c8), max_rows=1)
        row = rows[0] if rows else {}
        ship_cost = float(row.get("total_ship_cost") or 0)
        ship_cost_calc = float(row.get("total_ship_cost_calculated") or 0)
        order_costs = float(row.get("total_order_costs") or 0)
        return format_response(
            data=[row] if row else [],
            summary={
                "invoice_count": int(row.get("invoice_count") or 0),
                "total_ship_cost": ship_cost,
                "total_ship_cost_calculated": ship_cost_calc,
                "ship_cost_delta": ship_cost - ship_cost_calc,
                "total_order_costs": order_costs,
                "period": f"{start_date}..{end_date}",
                "exclude_credits": exclude_credits,
                "paid_only": paid_only,
            },
        )
    except ValueError as e:
        logger.info("get_shipping_summary rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_shipping_summary failed")
        return format_response(data=[], error=f"Execution error: {e}")


_VALID_SHIPPING_GROUP_BY = (
    "total",
    "segment",
    "carrier",
    "shipment_method",
    "segment_carrier",
)

_SHIPPING_SEGMENT_CASE = (
    "CASE WHEN cs.CustSuppGroup = 'ND' THEN 'B2C' "
    "WHEN cs.CustSuppGroup IN ('G0','G1','G2','G3','G4','G5','G6') THEN 'B2B' "
    "ELSE 'Overig' END"
)

_SHIPPING_CARRIER_CASE = (
    "CASE WHEN si.ShipmentMethod LIKE 'DH%' THEN 'DHL' "
    "WHEN si.ShipmentMethod LIKE 'FED%' THEN 'FedEx' "
    "WHEN si.ShipmentMethod LIKE 'UP%' THEN 'UPS' "
    "ELSE 'Other' END"
)


def _shipping_select_and_group(group_by: str) -> tuple[str, str, list[str]]:
    """Return (select_cols, group_cols, dimension_keys) for a given group_by."""
    if group_by == "total":
        return "", "", []
    if group_by == "segment":
        return f"{_SHIPPING_SEGMENT_CASE} AS segment, ", f"{_SHIPPING_SEGMENT_CASE}", ["segment"]
    if group_by == "carrier":
        return f"{_SHIPPING_CARRIER_CASE} AS carrier, ", f"{_SHIPPING_CARRIER_CASE}", ["carrier"]
    if group_by == "shipment_method":
        return "si.ShipmentMethod, ", "si.ShipmentMethod", ["ShipmentMethod"]
    # segment_carrier
    return (
        f"{_SHIPPING_SEGMENT_CASE} AS segment, {_SHIPPING_CARRIER_CASE} AS carrier, ",
        f"{_SHIPPING_SEGMENT_CASE}, {_SHIPPING_CARRIER_CASE}",
        ["segment", "carrier"],
    )


def _fetch_shipping_analysis(
    start_date: str,
    end_date: str,
    group_by: str,
    exclude_credits: bool,
    paid_only: bool,
    segments: tuple[str, ...] | None,
    carriers: tuple[str, ...] | None,
) -> list[dict[str, Any]]:
    """Run the shipping-cost analysis query for one period. No LY merging."""
    start_c8 = admcms_date(start_date)
    end_c8 = admcms_date(end_date)
    select_cols, group_cols, _ = _shipping_select_and_group(group_by)

    # Scope filters (applied with si. prefix since we join CustSupplier)
    scope_parts: list[str] = []
    if exclude_credits:
        scope_parts.append("si.InvoiceType = '0'")
    if paid_only:
        scope_parts.append("si.FullyPaid = 1")
    scope_parts.append("(si.ShipCost > 0 OR si.ShipCostCalculated > 0)")

    # Segment filter via CustSuppGroup
    if segments:
        allowed_groups = []
        if "B2C" in segments:
            allowed_groups.append("'ND'")
        if "B2B" in segments:
            allowed_groups.extend(["'G0'", "'G1'", "'G2'", "'G3'", "'G4'", "'G5'", "'G6'"])
        if "IC" in segments:
            allowed_groups.append("'INTC'")
        if allowed_groups:
            scope_parts.append(f"cs.CustSuppGroup IN ({', '.join(allowed_groups)})")

    # Carrier filter
    if carriers:
        carrier_clauses = []
        for c in carriers:
            if c == "DHL":
                carrier_clauses.append("si.ShipmentMethod LIKE 'DH%'")
            elif c == "FedEx":
                carrier_clauses.append("si.ShipmentMethod LIKE 'FED%'")
            elif c == "UPS":
                carrier_clauses.append("si.ShipmentMethod LIKE 'UP%'")
        if carrier_clauses:
            scope_parts.append(f"({' OR '.join(carrier_clauses)})")

    scope_sql = " AND ".join(scope_parts)

    measures = (
        "COUNT(*) AS order_count, "
        "SUM(si.ShipCost) AS total_charged, "
        "AVG(si.ShipCost) AS avg_charged, "
        "SUM(si.ShipCostCalculated) AS total_carrier_cost, "
        "AVG(si.ShipCostCalculated) AS avg_carrier_cost, "
        "SUM(si.LineTotal) AS turnover"
    )

    sql = (
        f"SELECT {select_cols}{measures} "
        "FROM Salesinvoice si "
        "JOIN CustSupplier cs ON cs.CustSuppnr = si.Customer AND cs.CustSuppType = '1' "
        "WHERE si.InvoiceDate >= ? AND si.InvoiceDate < ? "
        f"AND {scope_sql} "
    )
    if group_cols:
        sql += f"GROUP BY {group_cols} ORDER BY total_charged DESC"
    return execute_query_params(sql, (start_c8, end_c8), max_rows=500)


def _enrich_shipping_row(row: dict) -> dict:
    total_charged = float(row.get("total_charged") or 0)
    total_carrier_cost = float(row.get("total_carrier_cost") or 0)
    turnover = float(row.get("turnover") or 0)
    shipping_margin = total_charged - total_carrier_cost
    order_count = int(row.get("order_count") or 0)
    return {
        **row,
        "shipping_margin": shipping_margin,
        "avg_shipping_margin": shipping_margin / order_count if order_count else 0.0,
        "ship_cost_pct_of_turnover": _pct(total_charged, turnover),
    }


def get_shipping_cost_analysis(
    start_date: str,
    end_date: str,
    group_by: str = "segment_carrier",
    include_ly: bool = False,
    exclude_credits: bool = True,
    paid_only: bool = True,
    segments: tuple[str, ...] | None = None,
    carriers: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Detailed shipping-cost analysis with YoY compare.

    Returns per group: order_count, total_charged (ShipCost), total_carrier_cost
    (ShipCostCalculated), avg_charged, avg_carrier_cost, shipping_margin,
    avg_shipping_margin, ship_cost_pct_of_turnover, turnover.

    `group_by`:
      - total — one row across all segments/carriers
      - segment — B2B / B2C / Overig
      - carrier — DHL / FedEx / UPS / Other (from ShipmentMethod prefix)
      - shipment_method — exact method code (FEDE, FEDP, UPSS, DHLP, etc.)
      - segment_carrier — default; cross tab of segment x carrier

    Default scope matches financial-reporting conventions: only fully paid
    regular invoices. Disable via `exclude_credits=False` / `paid_only=False`.

    `segments` optional filter: tuple like ("B2B",) or ("B2B", "B2C").
    `carriers` optional filter: tuple like ("FedEx",) or ("DHL", "UPS").
    """
    try:
        if group_by not in _VALID_SHIPPING_GROUP_BY:
            return format_response(
                data=[],
                error=(
                    f"Invalid group_by {group_by!r}; must be one of: "
                    f"{', '.join(_VALID_SHIPPING_GROUP_BY)}"
                ),
            )
        admcms_date(start_date)
        admcms_date(end_date)

        current_raw = _fetch_shipping_analysis(
            start_date,
            end_date,
            group_by,
            exclude_credits,
            paid_only,
            segments,
            carriers,
        )
        current = [_enrich_shipping_row(r) for r in current_raw]

        filter_info = {
            "period": f"{start_date}..{end_date}",
            "exclude_credits": exclude_credits,
            "paid_only": paid_only,
            "group_by": group_by,
        }

        if not include_ly:
            total_charged = sum(float(r.get("total_charged") or 0) for r in current)
            total_carrier = sum(float(r.get("total_carrier_cost") or 0) for r in current)
            total_orders = sum(int(r.get("order_count") or 0) for r in current)
            return format_response(
                data=current,
                summary={
                    **filter_info,
                    "row_count": len(current),
                    "order_count": total_orders,
                    "total_charged": total_charged,
                    "total_carrier_cost": total_carrier,
                    "shipping_margin": total_charged - total_carrier,
                    "avg_charged": (total_charged / total_orders) if total_orders else 0.0,
                    "avg_carrier_cost": (total_carrier / total_orders) if total_orders else 0.0,
                },
            )

        # include_ly
        ly_start = shift_year(start_date, -1)
        ly_end = shift_year(end_date, -1)
        ly_raw = _fetch_shipping_analysis(
            ly_start, ly_end, group_by, exclude_credits, paid_only, segments, carriers
        )
        ly = [_enrich_shipping_row(r) for r in ly_raw]

        # Build composite key for matching: all dimension fields concatenated
        _, _, dims = _shipping_select_and_group(group_by)

        def _key(row: dict) -> tuple:
            return tuple(row.get(d) for d in dims)

        ly_by_key = {_key(r): r for r in ly}
        merged: list[dict] = []
        seen: set = set()
        for cur in current:
            k = _key(cur)
            seen.add(k)
            ly_match = ly_by_key.get(k)
            ly_charged = float(ly_match.get("total_charged") or 0) if ly_match else 0.0
            ly_carrier = float(ly_match.get("total_carrier_cost") or 0) if ly_match else 0.0
            ly_orders = int(ly_match.get("order_count") or 0) if ly_match else 0
            ly_avg_charged = (ly_charged / ly_orders) if ly_orders else 0.0
            ly_avg_carrier = (ly_carrier / ly_orders) if ly_orders else 0.0
            ly_margin = ly_charged - ly_carrier
            cur_avg_charged = float(cur.get("avg_charged") or 0)
            cur_avg_carrier = float(cur.get("avg_carrier_cost") or 0)
            cur_margin = float(cur.get("shipping_margin") or 0)
            merged.append(
                {
                    **cur,
                    "order_count_ly": ly_orders,
                    "total_charged_ly": ly_charged,
                    "total_carrier_cost_ly": ly_carrier,
                    "avg_charged_ly": ly_avg_charged,
                    "avg_carrier_cost_ly": ly_avg_carrier,
                    "shipping_margin_ly": ly_margin,
                    "delta_avg_charged": cur_avg_charged - ly_avg_charged,
                    "delta_avg_carrier_cost": cur_avg_carrier - ly_avg_carrier,
                    "delta_carrier_cost_pct": _pct(
                        cur_avg_carrier - ly_avg_carrier, ly_avg_carrier
                    ),
                    "delta_shipping_margin": cur_margin - ly_margin,
                }
            )
        for k, ly_only in ly_by_key.items():
            if k in seen:
                continue
            merged.append(
                {
                    **{d: ly_only.get(d) for d in dims},
                    "order_count": 0,
                    "total_charged": 0.0,
                    "total_carrier_cost": 0.0,
                    "order_count_ly": int(ly_only.get("order_count") or 0),
                    "total_charged_ly": float(ly_only.get("total_charged") or 0),
                    "total_carrier_cost_ly": float(ly_only.get("total_carrier_cost") or 0),
                }
            )

        return format_response(
            data=merged,
            summary={
                **filter_info,
                "ly_period": f"{ly_start}..{ly_end}",
                "row_count": len(merged),
            },
        )
    except ValueError as e:
        logger.info("get_shipping_cost_analysis rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_shipping_cost_analysis failed")
        return format_response(data=[], error=f"Execution error: {e}")


# Known corrupt supplier — documented issue, excluded from purchase totals by default
_KNOWN_CORRUPT_SUPPLIERS: tuple[int, ...] = (81,)  # VDM PART — data corruption


def detect_price_anomalies(
    start_date: str,
    end_date: str,
    min_price: float = 10000.0,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Find invoice lines with suspicious (extreme) prices in a date range.

    Flags a line when SalesPrice, InvoiceAmountHC, or CostAmountBookedHC
    exceeds `min_price`. Threshold 10000 catches scanner errors (barcode
    in price field → ~€10^12) as well as general outliers.

    Paging via `offset`; `summary.next_offset` tells you where to fetch next.
    """
    try:
        effective_limit = max(1, min(int(limit), _MAX_LIMIT))
        effective_offset = max(0, int(offset))
        start_c8 = admcms_date(start_date)
        end_c8 = admcms_date(end_date)
        if min_price < 0:
            raise ValueError(f"min_price must be >= 0, got {min_price}")

        page_sql, page_params = paging_clause(effective_offset, effective_limit)
        sql = (
            "SELECT si.Invoicenr, si.Orderline, si.Productcode, "
            "si.ThisIinvoice AS qty, si.SalesPrice, si.InvoiceAmountHC, "
            "si.CostAmountBookedHC, inv.InvoiceDate, inv.Customer "
            "FROM SalesinvoiceLines si "
            "JOIN Salesinvoice inv ON inv.Invoicenr = si.Invoicenr "
            "WHERE inv.InvoiceDate >= ? AND inv.InvoiceDate < ? "
            "AND (si.SalesPrice > ? OR si.InvoiceAmountHC > ? "
            "OR si.CostAmountBookedHC > ?) "
            "ORDER BY si.SalesPrice DESC" + page_sql
        )
        params = (start_c8, end_c8, min_price, min_price, min_price, *page_params)
        rows = execute_query_params(sql, params, max_rows=effective_limit)
        max_found = max((float(r.get("SalesPrice") or 0) for r in rows), default=0.0)
        summary = {
            "count": len(rows),
            "period": f"{start_date}..{end_date}",
            "min_price_threshold": min_price,
            "max_price_found": max_found,
            **paging_summary(effective_offset, effective_limit, len(rows)),
        }
        return format_response(data=rows, summary=summary)
    except ValueError as e:
        logger.info("detect_price_anomalies rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("detect_price_anomalies failed")
        return format_response(data=[], error=f"Execution error: {e}")


def get_stock_levels(
    product_code: str | None = None,
    warehouse: str | None = None,
    only_available: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Return stock levels from ProductWarehouse per product per warehouse.

    Filters are optional and can be combined. Known warehouse codes:
    'CM', 'DS', 'IN', 'OS', 'SA', 'DM', 'SC', 'ML', 'PR', 'TP'.

    Paging via `offset`; `summary.next_offset` indicates next page start.
    """
    try:
        effective_limit = max(1, min(int(limit), _MAX_LIMIT))
        effective_offset = max(0, int(offset))
        where_parts: list[str] = ["Active = 1"]
        params: list[Any] = []
        if product_code is not None:
            where_parts.append("Productcode = ?")
            params.append(product_code)
        if warehouse is not None:
            where_parts.append("Warehouse = ?")
            params.append(warehouse)
        if only_available:
            where_parts.append("ActualStock > 0")
        where_sql = " AND ".join(where_parts)
        page_sql, page_params = paging_clause(effective_offset, effective_limit)
        sql = (
            "SELECT Productcode, Warehouse, ActualStock, ActualStockValue, "
            "CostPrice, MinimumStock, MaximumStock, DefLocation "
            "FROM ProductWarehouse "
            f"WHERE {where_sql} "
            "ORDER BY ActualStockValue DESC" + page_sql
        )
        rows = execute_query_params(sql, tuple(params) + page_params, max_rows=effective_limit)
        total_qty = sum(int(r.get("ActualStock") or 0) for r in rows)
        total_value = sum(float(r.get("ActualStockValue") or 0) for r in rows)
        summary = {
            "count": len(rows),
            "total_qty": total_qty,
            "total_value": total_value,
            "product_code": product_code,
            "warehouse": warehouse,
            "only_available": only_available,
            **paging_summary(effective_offset, effective_limit, len(rows)),
        }
        return format_response(data=rows, summary=summary)
    except ValueError as e:
        logger.info("get_stock_levels rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_stock_levels failed")
        return format_response(data=[], error=f"Execution error: {e}")


def get_supplier_purchases(
    start_date: str,
    end_date: str,
    limit: int = 10,
    include_corrupt: bool = False,
) -> dict[str, Any]:
    """Return the top suppliers by purchase amount between two dates.

    Based on PurchaseOrderInvoice (supplier invoices), joined with
    CUSTSUPPLIER to get supplier names. Filters to CustSuppType='2'.

    Amounts are in home currency. `GoodsAmount` is the material cost;
    VAT and OtherCostTotal are also summed for full picture.

    Supplier 81 (VDM PART) is excluded by default due to known data
    corruption in ADMCMS; pass `include_corrupt=True` to include it.

    Args:
        start_date: ISO YYYY-MM-DD, inclusive.
        end_date: ISO YYYY-MM-DD, exclusive.
        limit: max suppliers to return, clamped 1-500, default 10.
        include_corrupt: include known-corrupt suppliers. Default False.
    """
    try:
        effective_limit = max(1, min(int(limit), _MAX_LIMIT))
        start_c8 = admcms_date(start_date)
        end_c8 = admcms_date(end_date)

        where_parts = [
            "poi.Date_ >= ?",
            "poi.Date_ < ?",
            "cs.CustSuppType = '2'",
        ]
        params: list[Any] = [start_c8, end_c8]
        if not include_corrupt and _KNOWN_CORRUPT_SUPPLIERS:
            placeholders = ", ".join("?" for _ in _KNOWN_CORRUPT_SUPPLIERS)
            where_parts.append(f"cs.CustSuppnr NOT IN ({placeholders})")
            params.extend(_KNOWN_CORRUPT_SUPPLIERS)
        where_sql = " AND ".join(where_parts)

        sql = (
            f"SELECT TOP ({effective_limit}) cs.CustSuppnr, cs.Searchname, "
            "COUNT(DISTINCT poi.Invoice) AS invoice_count, "
            "SUM(poi.GoodsAmount) AS total_goods, "
            "SUM(poi.VATAmount) AS total_vat, "
            "SUM(poi.OtherCostTotal) AS total_other_costs "
            "FROM PurchaseOrderInvoice poi "
            "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = poi.Supplier "
            f"WHERE {where_sql} "
            "GROUP BY cs.CustSuppnr, cs.Searchname "
            "ORDER BY total_goods DESC"
        )
        rows = execute_query_params(sql, tuple(params), max_rows=effective_limit)
        total = sum(float(r.get("total_goods") or 0) for r in rows)
        return format_response(
            data=rows,
            summary={
                "top_total_goods": total,
                "count": len(rows),
                "period": f"{start_date}..{end_date}",
                "corrupt_excluded": list(_KNOWN_CORRUPT_SUPPLIERS) if not include_corrupt else [],
            },
        )
    except ValueError as e:
        logger.info("get_supplier_purchases rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_supplier_purchases failed")
        return format_response(data=[], error=f"Execution error: {e}")


def get_customer_orders(
    customer_id: int | None = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """Return recent sales orders for a customer (or all customers if id is None).

    Includes SalesOrdersystemStatus so callers can post-filter further.
    Paging via `offset`; `summary.next_offset` tells you where next page starts.
    """
    try:
        effective_limit = max(1, min(int(limit), _MAX_LIMIT))
        effective_offset = max(0, int(offset))
        where_parts: list[str] = []
        params: list[Any] = []
        if customer_id is not None:
            where_parts.append("soh.Customer = ?")
            params.append(customer_id)
        where_sql = " AND ".join(where_parts) if where_parts else "1=1"
        page_sql, page_params = paging_clause(effective_offset, effective_limit)

        sql = (
            "SELECT soh.Ordernr, soh.Customer, cs.Searchname, soh.OrderDate, "
            "soh.DeliveryDate, soh.OrderAmount, soh.SalesOrdersystemStatus "
            "FROM SalesOrderHeader soh "
            "LEFT JOIN CustSupplier cs ON cs.CustSuppnr = soh.Customer "
            f"WHERE {where_sql} "
            "ORDER BY soh.OrderDate DESC" + page_sql
        )
        rows = execute_query_params(sql, tuple(params) + page_params, max_rows=effective_limit)
        total_value = sum(float(r.get("OrderAmount") or 0) for r in rows)
        summary = {
            "count": len(rows),
            "total_order_value": total_value,
            "customer_id": customer_id,
            **paging_summary(effective_offset, effective_limit, len(rows)),
        }
        return format_response(data=rows, summary=summary)
    except ValueError as e:
        logger.info("get_customer_orders rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_customer_orders failed")
        return format_response(data=[], error=f"Execution error: {e}")


# =========================================================================
# Analytics batch 2: credit_note / payment_method / salesrep / discount /
# order_origin — all share the shift_year / _pct / simple YoY-merge pattern
# =========================================================================

_VALID_CREDIT_NOTE_GROUP_BY = ("segment", "country", "customer", "make", "total", "month")


def _fetch_credit_notes(start_date: str, end_date: str, group_by: str) -> list[dict[str, Any]]:
    """Credit-invoice aggregation (InvoiceType='1'). Returns rows per group."""
    start_c8 = admcms_date(start_date)
    end_c8 = admcms_date(end_date)
    where_base = "WHERE si.InvoiceType = '1' AND si.InvoiceDate >= ? AND si.InvoiceDate < ?"
    params = (start_c8, end_c8)

    measures = (
        "COUNT(*) AS credit_count, "
        "SUM(si.LineTotal) AS total_credit_amount, "
        "AVG(si.LineTotal) AS avg_credit_amount, "
        "SUM(CASE WHEN si.LineTotal > 0 THEN 1 ELSE 0 END) AS positive_credit_count"
    )

    if group_by == "total":
        sql = f"SELECT {measures} FROM Salesinvoice si {where_base}"
        rows = execute_query_params(sql, params, max_rows=1)
        return rows or [
            {
                "credit_count": 0,
                "total_credit_amount": 0,
                "avg_credit_amount": 0,
                "positive_credit_count": 0,
            }
        ]

    if group_by == "segment":
        sql = (
            f"SELECT {_MARGIN_SEGMENT_CASE} AS segment, {measures} "
            f"FROM Salesinvoice si "
            "JOIN CustSupplier cs ON cs.CustSuppnr = si.Customer "
            f"{where_base} "
            f"GROUP BY {_MARGIN_SEGMENT_CASE} ORDER BY credit_count DESC"
        )
    elif group_by == "month":
        sql = (
            f"SELECT SUBSTRING(si.InvoiceDate, 1, 6) AS period, {measures} "
            f"FROM Salesinvoice si {where_base} "
            "GROUP BY SUBSTRING(si.InvoiceDate, 1, 6) "
            "ORDER BY period"
        )
    elif group_by == "country":
        sql = (
            f"SELECT si.DeliveryCountry, {measures} "
            f"FROM Salesinvoice si {where_base} "
            "GROUP BY si.DeliveryCountry ORDER BY credit_count DESC"
        )
    elif group_by == "customer":
        sql = (
            f"SELECT cs.Searchname, {measures} "
            f"FROM Salesinvoice si "
            "JOIN CustSupplier cs ON cs.CustSuppnr = si.Customer "
            f"{where_base} "
            "GROUP BY cs.Searchname ORDER BY credit_count DESC"
        )
    else:  # make
        sql = (
            f"SELECT p.Make, COUNT(DISTINCT si.Invoicenr) AS credit_count, "
            "SUM(sil.InvoiceAmountHC) AS total_credit_amount, "
            "AVG(sil.InvoiceAmountHC) AS avg_credit_amount, "
            "SUM(CASE WHEN sil.InvoiceAmountHC > 0 THEN 1 ELSE 0 END) AS positive_credit_count "
            "FROM Salesinvoice si "
            "JOIN SalesinvoiceLines sil ON sil.Invoicenr = si.Invoicenr "
            "JOIN PRODUCT p ON p.Productcode = sil.Productcode "
            f"{where_base} "
            "GROUP BY p.Make ORDER BY credit_count DESC"
        )
    return execute_query_params(sql, params, max_rows=500)


def get_credit_note_analysis(
    start_date: str,
    end_date: str,
    group_by: str = "segment",
    include_ly: bool = False,
) -> dict[str, Any]:
    """Credit-invoice (InvoiceType='1') analysis per dimension.

    Detects anomalies via `positive_credit_count` — credit notes normally
    have negative LineTotal; a positive value suggests a correction-of-
    correction or data issue worth reviewing.

    `group_by`: total, segment (B2C/B2B/IC/Overig), country, customer,
        make (joins SalesinvoiceLines + PRODUCT), month.
    `include_ly`: compare with same period one year earlier.
    """
    try:
        if group_by not in _VALID_CREDIT_NOTE_GROUP_BY:
            return format_response(
                data=[],
                error=(
                    f"Invalid group_by {group_by!r}; must be one of: "
                    f"{', '.join(_VALID_CREDIT_NOTE_GROUP_BY)}"
                ),
            )
        admcms_date(start_date)
        admcms_date(end_date)
        current = _fetch_credit_notes(start_date, end_date, group_by)

        def _summary(rows: list[dict], period_label: str) -> dict:
            total_cnt = sum(int(r.get("credit_count") or 0) for r in rows)
            total_amt = sum(float(r.get("total_credit_amount") or 0) for r in rows)
            pos_cnt = sum(int(r.get("positive_credit_count") or 0) for r in rows)
            return {
                "period": period_label,
                "group_by": group_by,
                "row_count": len(rows),
                "total_credit_count": total_cnt,
                "total_credit_amount": total_amt,
                "positive_credit_count_total": pos_cnt,
                "anomaly_pct": _pct(pos_cnt, total_cnt),
            }

        if not include_ly:
            return format_response(
                data=current,
                summary=_summary(current, f"{start_date}..{end_date}"),
            )

        ly_start = shift_year(start_date, -1)
        ly_end = shift_year(end_date, -1)
        ly_rows = _fetch_credit_notes(ly_start, ly_end, group_by)

        key_field = {
            "segment": "segment",
            "country": "DeliveryCountry",
            "customer": "Searchname",
            "make": "Make",
            "month": "period",
        }.get(group_by)

        if group_by == "total":
            cur = current[0]
            ly = ly_rows[0]
            cur_amt = float(cur.get("total_credit_amount") or 0)
            ly_amt = float(ly.get("total_credit_amount") or 0)
            return format_response(
                data=[
                    {
                        **cur,
                        "total_credit_amount_ly": ly_amt,
                        "credit_count_ly": int(ly.get("credit_count") or 0),
                        "delta_amount": cur_amt - ly_amt,
                        "delta_pct": _pct(cur_amt - ly_amt, ly_amt),
                    }
                ],
                summary={
                    **_summary(current, f"{start_date}..{end_date}"),
                    "ly_period": f"{ly_start}..{ly_end}",
                    "total_credit_amount_ly": ly_amt,
                },
            )

        ly_by_key = {r.get(key_field): r for r in ly_rows}
        merged = []
        seen = set()
        for cur in current:
            k = cur.get(key_field)
            seen.add(k)
            ly = ly_by_key.get(k)
            ly_amt = float(ly.get("total_credit_amount") or 0) if ly else 0.0
            cur_amt = float(cur.get("total_credit_amount") or 0)
            merged.append(
                {
                    **cur,
                    "total_credit_amount_ly": ly_amt,
                    "credit_count_ly": int(ly.get("credit_count") or 0) if ly else 0,
                    "delta_amount": cur_amt - ly_amt,
                    "delta_pct": _pct(cur_amt - ly_amt, ly_amt),
                }
            )
        return format_response(
            data=merged,
            summary={
                **_summary(current, f"{start_date}..{end_date}"),
                "ly_period": f"{ly_start}..{ly_end}",
            },
        )
    except ValueError as e:
        logger.info("get_credit_note_analysis rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_credit_note_analysis failed")
        return format_response(data=[], error=f"Execution error: {e}")


def _fetch_payment_methods(start_date: str, end_date: str) -> list[dict[str, Any]]:
    start_c8 = admcms_date(start_date)
    end_c8 = admcms_date(end_date)
    sql = (
        "SELECT PaymentCondition, "
        "COUNT(*) AS invoice_count, "
        "SUM(LineTotal) AS total_amount, "
        "AVG(LineTotal) AS avg_amount, "
        "AVG(CAST(PaymentDays AS float)) AS avg_payment_days, "
        "SUM(CASE WHEN FullyPaid = 1 THEN 1 ELSE 0 END) AS fully_paid_count "
        "FROM Salesinvoice "
        "WHERE InvoiceType = '0' "
        "AND InvoiceDate >= ? AND InvoiceDate < ? "
        "GROUP BY PaymentCondition ORDER BY invoice_count DESC"
    )
    return execute_query_params(sql, (start_c8, end_c8), max_rows=100)


def get_payment_method_stats(
    start_date: str, end_date: str, include_ly: bool = False
) -> dict[str, Any]:
    """Payment-method mix per invoice (PREP/AC/CC/LINK/etc.).

    Known PaymentCondition codes from schema:
      PREP (prepayment), AC (30d), ACD/ACC (ac + discount), CC (credit card),
      LINK (payment link), RFI (reference).

    Per code returns: invoice_count, total_amount, avg_amount, avg_payment_days,
    pct_fullypaid (=fully_paid_count/invoice_count), pct_of_total_count.
    """
    try:
        admcms_date(start_date)
        admcms_date(end_date)
        current = _fetch_payment_methods(start_date, end_date)
        total_current = sum(int(r.get("invoice_count") or 0) for r in current)

        def _enrich(row: dict, grand_total: int) -> dict:
            cnt = int(row.get("invoice_count") or 0)
            paid = int(row.get("fully_paid_count") or 0)
            return {
                **row,
                "pct_fullypaid": _pct(paid, cnt),
                "pct_of_total_count": _pct(cnt, grand_total),
            }

        enriched = [_enrich(r, total_current) for r in current]

        if not include_ly:
            return format_response(
                data=enriched,
                summary={
                    "period": f"{start_date}..{end_date}",
                    "row_count": len(enriched),
                    "total_invoice_count": total_current,
                    "total_amount": sum(float(r.get("total_amount") or 0) for r in current),
                },
            )

        ly_start = shift_year(start_date, -1)
        ly_end = shift_year(end_date, -1)
        ly_rows = _fetch_payment_methods(ly_start, ly_end)
        total_ly = sum(int(r.get("invoice_count") or 0) for r in ly_rows)
        ly_by_key = {r.get("PaymentCondition"): r for r in ly_rows}
        merged = []
        for r in enriched:
            k = r.get("PaymentCondition")
            ly = ly_by_key.get(k)
            ly_cnt = int(ly.get("invoice_count") or 0) if ly else 0
            ly_total = float(ly.get("total_amount") or 0) if ly else 0.0
            merged.append(
                {
                    **r,
                    "invoice_count_ly": ly_cnt,
                    "total_amount_ly": ly_total,
                    "pct_of_total_count_ly": _pct(ly_cnt, total_ly),
                    "delta_count": int(r.get("invoice_count") or 0) - ly_cnt,
                    "delta_count_pct": _pct(int(r.get("invoice_count") or 0) - ly_cnt, ly_cnt),
                }
            )
        return format_response(
            data=merged,
            summary={
                "period": f"{start_date}..{end_date}",
                "ly_period": f"{ly_start}..{ly_end}",
                "total_invoice_count": total_current,
                "total_invoice_count_ly": total_ly,
                "row_count": len(merged),
            },
        )
    except ValueError as e:
        logger.info("get_payment_method_stats rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_payment_method_stats failed")
        return format_response(data=[], error=f"Execution error: {e}")


def _fetch_salesrep(start_date: str, end_date: str) -> list[dict[str, Any]]:
    start_c8 = admcms_date(start_date)
    end_c8 = admcms_date(end_date)
    sql = (
        "SELECT Salesrep, "
        "COUNT(*) AS invoice_count, "
        "COUNT(DISTINCT Customer) AS unique_customers, "
        "SUM(LineTotal) AS total_turnover, "
        "AVG(LineTotal) AS avg_order_value "
        "FROM Salesinvoice "
        "WHERE InvoiceType = '0' AND FullyPaid = 1 "
        "AND InvoiceDate >= ? AND InvoiceDate < ? "
        "GROUP BY Salesrep ORDER BY total_turnover DESC"
    )
    return execute_query_params(sql, (start_c8, end_c8), max_rows=200)


def get_salesrep_performance(
    start_date: str, end_date: str, include_ly: bool = False
) -> dict[str, Any]:
    """Per-salesrep performance: invoices, unique customers, turnover, AOV.

    Scope: paid regular invoices only (matches financial-reporting convention).
    """
    try:
        admcms_date(start_date)
        admcms_date(end_date)
        current = _fetch_salesrep(start_date, end_date)

        if not include_ly:
            return format_response(
                data=current,
                summary={
                    "period": f"{start_date}..{end_date}",
                    "row_count": len(current),
                    "total_turnover": sum(float(r.get("total_turnover") or 0) for r in current),
                },
            )

        ly_start = shift_year(start_date, -1)
        ly_end = shift_year(end_date, -1)
        ly_rows = _fetch_salesrep(ly_start, ly_end)
        ly_by_key = {r.get("Salesrep"): r for r in ly_rows}
        merged = []
        seen = set()
        for r in current:
            k = r.get("Salesrep")
            seen.add(k)
            ly = ly_by_key.get(k)
            ly_turn = float(ly.get("total_turnover") or 0) if ly else 0.0
            cur_turn = float(r.get("total_turnover") or 0)
            merged.append(
                {
                    **r,
                    "total_turnover_ly": ly_turn,
                    "invoice_count_ly": int(ly.get("invoice_count") or 0) if ly else 0,
                    "delta_turnover": cur_turn - ly_turn,
                    "delta_pct": _pct(cur_turn - ly_turn, ly_turn),
                }
            )
        return format_response(
            data=merged,
            summary={
                "period": f"{start_date}..{end_date}",
                "ly_period": f"{ly_start}..{ly_end}",
                "row_count": len(merged),
            },
        )
    except ValueError as e:
        logger.info("get_salesrep_performance rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_salesrep_performance failed")
        return format_response(data=[], error=f"Execution error: {e}")


_VALID_DISCOUNT_GROUP_BY = ("segment", "customer", "make", "productgroup", "salesrep", "total")


def _fetch_discount(
    start_date: str, end_date: str, group_by: str, filters: dict | None = None
) -> list[dict[str, Any]]:
    """`filters` (optional, additive): supports customer_group, business_unit,
    make, productgroup on every group_by (adds CUSTSUPPLIER/PRODUCT joins on
    demand when the chosen group_by hasn't already added one). `country` is
    NOT supported by this tool (out of scope for this slice) and is ignored.
    """
    start_c8 = admcms_date(start_date)
    end_c8 = admcms_date(end_date)
    where = (
        "WHERE si.InvoiceType = '0' "
        "AND si.InvoiceDate >= ? AND si.InvoiceDate < ? "
        "AND sil.DiscountAmount > 0"
    )
    # Gross (pre-discount) = NetAmountHC + DiscountAmount
    measures = (
        "COUNT(*) AS line_count, "
        "SUM(sil.InvoiceAmountHC) AS net_turnover, "
        "SUM(sil.DiscountAmount) AS total_discount, "
        "AVG(sil.DiscountAmount) AS avg_discount"
    )
    base_from = "FROM SalesinvoiceLines sil JOIN Salesinvoice si ON si.Invoicenr = sil.Invoicenr "

    has_cs = group_by in ("segment", "customer")
    has_p = group_by in ("make", "productgroup")
    needs_cs = _wants_cs(filters) and not has_cs
    needs_p = _wants_p(filters) and not has_p

    extra_join = ""
    if needs_cs:
        extra_join += "JOIN CustSupplier cs ON cs.CustSuppnr = si.Customer "
    if needs_p:
        extra_join += "JOIN PRODUCT p ON p.Productcode = sil.Productcode "

    filter_sql, filter_params = build_filter_where(
        filters,
        cs="cs" if (has_cs or needs_cs) else None,
        p="p" if (has_p or needs_p) else None,
    )
    params = (start_c8, end_c8) + filter_params

    if group_by == "total":
        sql = f"SELECT {measures} {base_from}{extra_join}{where}{filter_sql}"
        rows = execute_query_params(sql, params, max_rows=1)
        return rows or [
            {"line_count": 0, "net_turnover": 0, "total_discount": 0, "avg_discount": 0}
        ]

    if group_by == "segment":
        sql = (
            f"SELECT {_MARGIN_SEGMENT_CASE} AS segment, {measures} "
            f"{base_from}"
            f"JOIN CustSupplier cs ON cs.CustSuppnr = si.Customer {extra_join}"
            f"{where}{filter_sql} "
            f"GROUP BY {_MARGIN_SEGMENT_CASE} ORDER BY total_discount DESC"
        )
    elif group_by == "customer":
        sql = (
            f"SELECT cs.Searchname, {measures} "
            f"{base_from}"
            f"JOIN CustSupplier cs ON cs.CustSuppnr = si.Customer {extra_join}"
            f"{where}{filter_sql} "
            "GROUP BY cs.Searchname ORDER BY total_discount DESC"
        )
    elif group_by == "make":
        sql = (
            f"SELECT p.Make, {measures} "
            f"{base_from}"
            f"JOIN PRODUCT p ON p.Productcode = sil.Productcode {extra_join}"
            f"{where}{filter_sql} "
            "GROUP BY p.Make ORDER BY total_discount DESC"
        )
    elif group_by == "productgroup":
        sql = (
            f"SELECT p.Productgroup, {measures} "
            f"{base_from}"
            f"JOIN PRODUCT p ON p.Productcode = sil.Productcode {extra_join}"
            f"{where}{filter_sql} "
            "GROUP BY p.Productgroup ORDER BY total_discount DESC"
        )
    else:  # salesrep
        sql = (
            f"SELECT si.Salesrep, {measures} "
            f"{base_from}{extra_join}{where}{filter_sql} "
            "GROUP BY si.Salesrep ORDER BY total_discount DESC"
        )
    return execute_query_params(sql, params, max_rows=500)


def get_discount_analysis(
    start_date: str,
    end_date: str,
    group_by: str = "segment",
    filters: dict | None = None,
) -> dict[str, Any]:
    """Discount analysis on SalesinvoiceLines (DiscountAmount > 0).

    Gross pre-discount = net_turnover + total_discount. Discount %
    is calculated against that gross.

    `group_by`: segment, customer, make, productgroup, salesrep, total.

    `filters` (optional dict, additive): supports customer_group,
    business_unit, make, productgroup. `country` is not supported by this
    tool and is ignored if passed.
    """
    try:
        if group_by not in _VALID_DISCOUNT_GROUP_BY:
            return format_response(
                data=[],
                error=(
                    f"Invalid group_by {group_by!r}; must be one of: "
                    f"{', '.join(_VALID_DISCOUNT_GROUP_BY)}"
                ),
            )
        admcms_date(start_date)
        admcms_date(end_date)
        rows = _fetch_discount(start_date, end_date, group_by, filters=filters)

        def _enrich(row: dict) -> dict:
            net = float(row.get("net_turnover") or 0)
            disc = float(row.get("total_discount") or 0)
            gross = net + disc
            return {
                **row,
                "gross_turnover": gross,
                "discount_pct": _pct(disc, gross),
            }

        enriched = [_enrich(r) for r in rows]
        total_disc = sum(float(r.get("total_discount") or 0) for r in rows)
        total_net = sum(float(r.get("net_turnover") or 0) for r in rows)
        total_gross = total_net + total_disc
        return format_response(
            data=enriched,
            summary={
                "period": f"{start_date}..{end_date}",
                "group_by": group_by,
                "row_count": len(enriched),
                "total_discount": total_disc,
                "total_net_turnover": total_net,
                "total_gross_turnover": total_gross,
                "avg_discount_pct": _pct(total_disc, total_gross),
            },
        )
    except ValueError as e:
        logger.info("get_discount_analysis rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_discount_analysis failed")
        return format_response(data=[], error=f"Execution error: {e}")


def _fetch_order_origin(
    start_date: str, end_date: str, filters: dict | None = None
) -> list[dict[str, Any]]:
    """`filters` (optional, additive): supports `country` (si.DeliveryCountry)
    and `customer_group`/`business_unit` (adds a CUSTSUPPLIER join when
    requested). `make`/`productgroup` are not supported (no PRODUCT join).
    """
    start_c8 = admcms_date(start_date)
    end_c8 = admcms_date(end_date)
    needs_cs = _wants_cs(filters)
    join_clause = "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer " if needs_cs else ""
    filter_sql, filter_params = build_filter_where(
        filters, cs="cs" if needs_cs else None, country_col="si.DeliveryCountry"
    )
    sql = (
        "SELECT sil.OrderOrigin, "
        "COUNT(*) AS line_count, "
        "COUNT(DISTINCT sil.Invoicenr) AS invoice_count, "
        "SUM(sil.InvoiceAmountHC) AS total_amount "
        "FROM SalesinvoiceLines sil "
        "JOIN Salesinvoice si ON si.Invoicenr = sil.Invoicenr "
        f"{join_clause}"
        "WHERE si.InvoiceType = '0' "
        "AND si.InvoiceDate >= ? AND si.InvoiceDate < ?" + filter_sql + " "
        "GROUP BY sil.OrderOrigin ORDER BY line_count DESC"
    )
    return execute_query_params(sql, (start_c8, end_c8) + filter_params, max_rows=100)


def get_order_origin_mix(
    start_date: str,
    end_date: str,
    include_ly: bool = False,
    filters: dict | None = None,
) -> dict[str, Any]:
    """Order-channel mix by SalesinvoiceLines.OrderOrigin.

    Known dominant codes from schema: '06' (web, ~88% of lines), '12', '11',
    '01', ''. Per origin returns line_count, invoice_count, total_amount,
    percentage of total lines.

    `filters` (optional dict, additive): supports `country` and
    `customer_group`/`business_unit` (adds a CUSTSUPPLIER join). `make`/
    `productgroup` are not supported by this tool and are ignored if passed.
    """
    try:
        admcms_date(start_date)
        admcms_date(end_date)
        current = _fetch_order_origin(start_date, end_date, filters=filters)
        total_current = sum(int(r.get("line_count") or 0) for r in current)

        def _enrich(row: dict, grand: int) -> dict:
            return {
                **row,
                "pct_of_total_lines": _pct(int(row.get("line_count") or 0), grand),
            }

        enriched = [_enrich(r, total_current) for r in current]
        if not include_ly:
            return format_response(
                data=enriched,
                summary={
                    "period": f"{start_date}..{end_date}",
                    "row_count": len(enriched),
                    "total_line_count": total_current,
                    "total_amount": sum(float(r.get("total_amount") or 0) for r in current),
                },
            )

        ly_start = shift_year(start_date, -1)
        ly_end = shift_year(end_date, -1)
        ly_rows = _fetch_order_origin(ly_start, ly_end, filters=filters)
        total_ly = sum(int(r.get("line_count") or 0) for r in ly_rows)
        ly_by_key = {r.get("OrderOrigin"): r for r in ly_rows}
        merged = []
        for r in enriched:
            k = r.get("OrderOrigin")
            ly = ly_by_key.get(k)
            ly_lines = int(ly.get("line_count") or 0) if ly else 0
            cur_lines = int(r.get("line_count") or 0)
            merged.append(
                {
                    **r,
                    "line_count_ly": ly_lines,
                    "pct_of_total_lines_ly": _pct(ly_lines, total_ly),
                    "delta_line_count": cur_lines - ly_lines,
                    "delta_pct": _pct(cur_lines - ly_lines, ly_lines),
                }
            )
        return format_response(
            data=merged,
            summary={
                "period": f"{start_date}..{end_date}",
                "ly_period": f"{ly_start}..{ly_end}",
                "total_line_count": total_current,
                "total_line_count_ly": total_ly,
                "row_count": len(merged),
            },
        )
    except ValueError as e:
        logger.info("get_order_origin_mix rejected: %s", e)
        return format_response(data=[], error=str(e))
    except Exception as e:
        logger.exception("get_order_origin_mix failed")
        return format_response(data=[], error=f"Execution error: {e}")


_DEAD_STOCK_GROUP_BY = ("product", "make", "productgroup")
_DEAD_STOCK_INACTIVE_WAREHOUSES = ("OS", "SA", "DM")
_DEAD_STOCK_MIN_MONTHS = 1
_DEAD_STOCK_MAX_MONTHS = 60


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


def _dead_stock_ctes(include_inactive_warehouses: bool) -> str:
    """Build the shared CTE block (last_sale + stock) for dead-stock queries.

    The block contains exactly one '?' placeholder: `ActualStockValue >= ?`
    (bound to min_stock_value by the outer query). The warehouse filter is
    interpolated as a literal clause from `_DEAD_STOCK_INACTIVE_WAREHOUSES`,
    not via a parameter — safe because the codes come from a module constant,
    never from user input.
    """
    if include_inactive_warehouses:
        warehouse_clause = ""
    else:
        codes = ", ".join(f"'{w}'" for w in _DEAD_STOCK_INACTIVE_WAREHOUSES)
        warehouse_clause = f" AND pw.Warehouse NOT IN ({codes})"
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
    return cte_sql


def _dead_stock_summary_sql(include_inactive_warehouses: bool) -> str:
    cte_sql = _dead_stock_ctes(include_inactive_warehouses)
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
    cte_sql = _dead_stock_ctes(include_inactive_warehouses)
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
    cte_sql = _dead_stock_ctes(include_inactive_warehouses)
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


def _shape_dead_stock_aggregated_row(row: dict[str, Any]) -> dict[str, Any]:
    """Aggregated rows already have the right keys; just round avg_months."""
    out = dict(row)
    avg = out.get("avg_months_since_sale")
    if avg is not None:
        out["avg_months_since_sale"] = round(float(avg), 1)
    return out


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
            shape_fn = _shape_dead_stock_row
        else:
            main_sql_base = _dead_stock_main_sql_aggregated(
                group_by, include_inactive_warehouses
            )
            shape_fn = _shape_dead_stock_aggregated_row

        page_sql, page_params = paging_clause(effective_offset, effective_limit)
        main_sql = main_sql_base + page_sql
        # Params: min_stock_value, cutoff, offset, limit
        main_params = (effective_min_value, cutoff_date, *page_params)
        raw_rows = execute_query_params(main_sql, main_params, max_rows=effective_limit)
        rows = _convert_rows([shape_fn(r) for r in raw_rows])

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


_FILTER_OPTIONS_BUSINESS_UNITS = ["B2C", "B2B", "IC"]

# Dropdown values change rarely, but the underlying queries do full-table scans
# (PRODUCT.Make cold-scans in ~40s). Cache a fully-successful result for this long
# so the expensive scan runs at most once per interval. Overridable for tests.
_FILTER_OPTIONS_TTL_SECONDS = 6 * 60 * 60

# One query per data dimension. Kept independent so a slow/timing-out query on one
# dimension (e.g. make) cannot discard the others' already-fetched values.
_FILTER_OPTION_DIMS = (
    (
        "customer_group",
        "CustSuppGroup",
        "SELECT TOP (30) CustSuppGroup "
        "FROM CUSTSUPPLIER "
        "WHERE CustSuppType = '1' AND CustSuppGroup IS NOT NULL "
        "GROUP BY CustSuppGroup "
        "ORDER BY COUNT(*) DESC",
        30,
    ),
    (
        "country",
        "DeliveryCountry",
        "SELECT TOP (50) DeliveryCountry "
        "FROM SALESINVOICE "
        "WHERE DeliveryCountry IS NOT NULL "
        "GROUP BY DeliveryCountry "
        "ORDER BY COUNT(*) DESC",
        50,
    ),
    (
        "make",
        "Make",
        "SELECT TOP (100) Make "
        "FROM PRODUCT "
        "WHERE Make IS NOT NULL "
        "GROUP BY Make "
        "ORDER BY COUNT(*) DESC",
        100,
    ),
    (
        "productgroup",
        "Productgroup",
        "SELECT TOP (100) Productgroup "
        "FROM PRODUCT "
        "WHERE Productgroup IS NOT NULL "
        "GROUP BY Productgroup "
        "ORDER BY COUNT(*) DESC",
        100,
    ),
)

_filter_options_lock = threading.Lock()
# {"data": <last result dict without business_unit/error>, "ts": <monotonic seconds>}.
# ts is only advanced to "fresh" on a fully-successful refresh; a partial/failed
# refresh keeps ts stale so the next call retries the missing dimensions.
_filter_options_cache: dict[str, Any] = {"data": None, "ts": 0.0}


def _reset_filter_options_cache() -> None:
    """Clear the in-process cache (used by tests to isolate cases)."""
    with _filter_options_lock:
        _filter_options_cache["data"] = None
        _filter_options_cache["ts"] = 0.0


def get_filter_options() -> dict[str, Any]:
    """Return distinct values for each filter dimension (for building UI dropdowns).

    Backs the `filters` arg on get_revenue / get_margin_report / get_kpi_summary /
    get_top_customers / get_order_origin_mix / get_discount_analysis.

    - `customer_group`: distinct CUSTSUPPLIER.CustSuppGroup for real customers
      (CustSuppType='1'), ordered by frequency, top 30.
    - `business_unit`: fixed set derived from customer_group (B2C/B2B/IC).
    - `country`: distinct SALESINVOICE.DeliveryCountry, ordered by frequency,
      top 50.
    - `make` / `productgroup`: distinct PRODUCT.Make / PRODUCT.Productgroup,
      ordered by frequency, top 100.

    Each dimension is queried independently: if one query fails or times out, the
    others still return, and the last known-good values for the failed dimension
    are reused. A fully-successful result is cached for ``_FILTER_OPTIONS_TTL_SECONDS``
    so the expensive cold table scans run at most once per interval.

    Returns `{customer_group, business_unit, country, make, productgroup}`, plus an
    `error` string listing any dimensions that failed on this refresh.
    """
    now = time.monotonic()
    cached = _filter_options_cache["data"]
    if cached is not None and (now - _filter_options_cache["ts"]) < _FILTER_OPTIONS_TTL_SECONDS:
        result = {k: list(v) for k, v in cached.items()}
        result["business_unit"] = list(_FILTER_OPTIONS_BUSINESS_UNITS)
        return result

    # Seed from last known-good so a transient failure keeps prior values.
    prev = cached or {}
    dims: dict[str, list[Any]] = {
        key: list(prev.get(key, [])) for key, _col, _sql, _cap in _FILTER_OPTION_DIMS
    }
    errors: dict[str, str] = {}
    for key, col, sql, cap in _FILTER_OPTION_DIMS:
        try:
            rows = execute_query_params(sql, (), max_rows=cap)
            vals = [r.get(col) for r in rows if r.get(col) is not None]
            if vals:
                dims[key] = vals
        except Exception as e:  # noqa: BLE001 - one bad dim must not sink the rest
            logger.warning("get_filter_options: %s query failed: %s", key, e)
            errors[key] = str(e)

    with _filter_options_lock:
        _filter_options_cache["data"] = {k: list(v) for k, v in dims.items()}
        # Only treat as fresh when every dimension succeeded; otherwise force a retry
        # on the next call instead of caching a partial result for the full TTL.
        _filter_options_cache["ts"] = now if not errors else 0.0

    result: dict[str, Any] = {k: list(v) for k, v in dims.items()}
    result["business_unit"] = list(_FILTER_OPTIONS_BUSINESS_UNITS)
    if errors:
        result["error"] = "Execution error: " + "; ".join(
            f"{k}: {v}" for k, v in errors.items()
        )
    return result
