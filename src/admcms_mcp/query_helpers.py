"""Shared helpers for building ADMCMS SQL queries.

Kept tiny and composition-friendly so each tool in tools.py can reuse
without pulling in business logic.
"""

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
