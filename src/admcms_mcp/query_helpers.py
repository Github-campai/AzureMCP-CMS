"""Shared helpers for building ADMCMS SQL queries.

Kept tiny and composition-friendly so each tool in tools.py can reuse
without pulling in business logic.
"""

import calendar
import re
from datetime import date
from typing import Any

_ISO_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def admcms_date(iso: str) -> str:
    """Convert an ISO date string to ADMCMS' char(8) format.

    >>> admcms_date("2026-04-18")
    '20260418'

    Raises ValueError for any input that is not exactly YYYY-MM-DD.
    """
    if not isinstance(iso, str) or not _ISO_PATTERN.match(iso):
        raise ValueError(f"Invalid ISO date: {iso!r}; expected YYYY-MM-DD")
    try:
        date.fromisoformat(iso)
    except ValueError as e:
        raise ValueError(f"Invalid ISO date: {iso!r}; {e}") from e
    return iso.replace("-", "")


def company_filter(company: str | None) -> tuple[str, tuple[str, ...]]:
    """Build a SQL WHERE-clause fragment for an optional company filter.

    Returns `(sql_fragment, params_tuple)`. Fragment is empty when `company`
    is None or empty, so concatenation stays safe.

    Column name `Company` is the current assumption. If the actual ADMCMS
    column turns out to be different (e.g. `Mandant`, `CompanyCode`), change
    this helper in one place — all callers pick it up automatically.
    """
    if not company:
        return "", ()
    return " AND Company = ?", (company,)


_SEGMENT_GROUPS = {
    "B2C": ["ND"],
    "B2B": ["G2", "G3", "G4", "G5", "G6"],
    "IC": ["INTC"],
}


def build_filter_where(
    filters: dict | None,
    *,
    cs: str | None = None,
    p: str | None = None,
    country_col: str | None = None,
) -> tuple[str, tuple]:
    """Build a SQL WHERE-clause fragment for optional dimensional filters.

    `filters` may carry any of `customer_group`, `business_unit`, `country`,
    `make`, `productgroup`. Each dim is only applied when its required
    alias/column is provided by the caller (so a tool only advertises the
    dims its query actually supports) and its value list is non-empty.
    Unknown dims are silently skipped.

    Returns `(sql_fragment, params)` where `sql_fragment` is either `""` or
    a string starting with `" AND "` ready to append to an existing WHERE
    clause, and `params` is a flat tuple of values in the same order as the
    `?` placeholders across the fragment.
    """
    if not filters:
        return "", ()

    fragments: list[str] = []
    params: list[Any] = []

    for dim, values in filters.items():
        if not values:
            continue

        column: str | None = None
        codes: list[Any] = []

        if dim == "customer_group" and cs:
            column = f"{cs}.CustSuppGroup"
            codes = list(values)
        elif dim == "business_unit" and cs:
            column = f"{cs}.CustSuppGroup"
            for unit in values:
                for code in _SEGMENT_GROUPS.get(unit, []):
                    if code not in codes:
                        codes.append(code)
        elif dim == "country" and country_col:
            column = country_col
            codes = list(values)
        elif dim == "make" and p:
            column = f"{p}.Make"
            codes = list(values)
        elif dim == "productgroup" and p:
            column = f"{p}.Productgroup"
            codes = list(values)

        if not column or not codes:
            continue

        placeholders = ",".join("?" * len(codes))
        fragments.append(f" AND {column} IN ({placeholders})")
        params.extend(codes)

    return "".join(fragments), tuple(params)


_LIKE_ESCAPE_MAP = str.maketrans(
    {
        "%": "[%]",
        "_": "[_]",
        "[": "[[]",
    }
)


def sanitize_like(term: str) -> str:
    """Escape T-SQL LIKE metacharacters so user input matches literally.

    Uses T-SQL's [...] character-class escaping (not backslash).
    Callers wrap the result with %...% themselves to enable wildcard search.
    """
    return term.translate(_LIKE_ESCAPE_MAP)


def format_response(
    data: list[Any],
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Shape tool responses consistently: data/summary/error."""
    return {"data": data, "summary": summary, "error": error}


def paging_clause(offset: int, limit: int) -> tuple[str, tuple[int, int]]:
    """SQL fragment + params for offset-based paging.

    Produces ` OFFSET ? ROWS FETCH NEXT ? ROWS ONLY`, which requires the
    query to have an ORDER BY clause earlier (T-SQL rule).
    """
    safe_offset = max(0, int(offset))
    safe_limit = max(1, int(limit))
    return " OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", (safe_offset, safe_limit)


def shift_year(iso: str, years: int) -> str:
    """Shift an ISO date by N years, handling Feb-29 → Feb-28 gracefully.

    >>> shift_year("2026-04-14", -1)
    '2025-04-14'
    >>> shift_year("2024-02-29", -1)
    '2023-02-28'
    """
    d = date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:
        # Only failure mode: Feb 29 → non-leap year. Step back to Feb 28.
        return d.replace(year=d.year + years, day=28).isoformat()


def paging_summary(offset: int, limit: int, returned: int) -> dict[str, Any]:
    """Standard paging keys to merge into a tool's `summary` dict.

    Uses a conservative `has_more` heuristic: if the page came back with
    exactly `limit` rows, assume there is a next page. Callers that need
    an exact count can run a separate COUNT query.
    """
    safe_offset = max(0, int(offset))
    safe_limit = max(1, int(limit))
    has_more = returned >= safe_limit
    return {
        "offset": safe_offset,
        "limit": safe_limit,
        "returned": returned,
        "has_more": has_more,
        "next_offset": (safe_offset + returned) if has_more else None,
    }


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


# ---------------------------------------------------------------------------
# Marking database text as external content
# ---------------------------------------------------------------------------

#: Delimiters around text that came out of ADMCMS rather than from the user.
#: Same pair the Campai Securitech MCP uses, so the convention reads the same
#: across servers and one instruction covers both.
EXTERNAL_OPEN = "⟦extern⟧"
EXTERNAL_CLOSE = "⟦/extern⟧"

#: Columns whose contents are typed by people into ADMCMS: product
#: descriptions, customer and supplier names. Anything a model could read as an
#: instruction arrives through one of these.
#:
#: Deliberately short. Codes, dates, amounts and warehouse letters cannot carry
#: a sentence, and marking them would bury the output in delimiters. Values that
#: are meant to be fed *back* to a tool — `get_lookups`, `get_filter_options` —
#: are never marked, because a filter value wrapped in delimiters no longer
#: matches anything.
EXTERNAL_TEXT_COLUMNS = frozenset({"Searchname", "Description"})


def mark_external(value: Any) -> Any:
    """Wrap a value in the external-content delimiters. Non-strings pass through."""
    if not isinstance(value, str) or not value.strip():
        return value
    if value.startswith(EXTERNAL_OPEN):
        return value
    return f"{EXTERNAL_OPEN}{value}{EXTERNAL_CLOSE}"


def mark_external_rows(rows: Any, columns: frozenset[str] = EXTERNAL_TEXT_COLUMNS) -> Any:
    """Mark the free-text columns of every row in place-safe fashion.

    Returns the input unchanged when it is not a list of dicts, so callers can
    hand it whatever a tool produced.
    """
    if not isinstance(rows, list):
        return rows
    for row in rows:
        if not isinstance(row, dict):
            continue
        for column in columns & row.keys():
            row[column] = mark_external(row[column])
    return rows
