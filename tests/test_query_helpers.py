import pytest

from admcms_mcp.query_helpers import (
    admcms_date,
    build_filter_where,
    company_filter,
    format_response,
    paging_clause,
    paging_summary,
    sanitize_like,
    shift_year,
    subtract_months,
)


class TestAdmcmsDate:
    def test_valid_iso_converts_to_char8(self):
        assert admcms_date("2026-04-18") == "20260418"

    def test_new_year_boundary(self):
        assert admcms_date("2026-01-01") == "20260101"

    def test_rejects_short_format(self):
        with pytest.raises(ValueError, match="Invalid ISO date"):
            admcms_date("2026-4-1")

    def test_rejects_long_format(self):
        with pytest.raises(ValueError, match="Invalid ISO date"):
            admcms_date("2026-04-18T12:00:00")

    def test_rejects_garbage(self):
        with pytest.raises(ValueError, match="Invalid ISO date"):
            admcms_date("nonsense")

    def test_rejects_invalid_month(self):
        with pytest.raises(ValueError, match="Invalid ISO date"):
            admcms_date("2026-13-01")


class TestCompanyFilter:
    def test_none_produces_empty_fragment(self):
        sql, params = company_filter(None)
        assert sql == ""
        assert params == ()

    def test_value_produces_and_clause(self):
        sql, params = company_filter("CMS001")
        assert sql == " AND Company = ?"
        assert params == ("CMS001",)

    def test_empty_string_treated_as_none(self):
        sql, params = company_filter("")
        assert sql == ""
        assert params == ()


class TestSanitizeLike:
    def test_escapes_percent(self):
        assert sanitize_like("50%") == "50[%]"

    def test_escapes_underscore(self):
        assert sanitize_like("item_3") == "item[_]3"

    def test_escapes_open_bracket(self):
        assert sanitize_like("a[b") == "a[[]b"

    def test_escapes_multiple(self):
        assert sanitize_like("%_[") == "[%][_][[]"

    def test_plain_string_unchanged(self):
        assert sanitize_like("MAHLE-FILTER") == "MAHLE-FILTER"

    def test_empty_string(self):
        assert sanitize_like("") == ""


class TestFormatResponse:
    def test_data_only(self):
        r = format_response(data=[{"a": 1}])
        assert r == {"data": [{"a": 1}], "summary": None, "error": None}

    def test_with_summary(self):
        r = format_response(data=[], summary={"count": 0})
        assert r == {"data": [], "summary": {"count": 0}, "error": None}

    def test_with_error(self):
        r = format_response(data=[], error="boom")
        assert r == {"data": [], "summary": None, "error": "boom"}

    def test_all_fields(self):
        r = format_response(data=[{"x": 1}], summary={"n": 1}, error=None)
        assert r["data"] == [{"x": 1}]
        assert r["summary"] == {"n": 1}
        assert r["error"] is None


class TestPagingClause:
    def test_produces_sql_and_params(self):
        sql, params = paging_clause(40, 20)
        assert "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY" in sql
        assert params == (40, 20)

    def test_clamps_negative_offset_to_zero(self):
        sql, params = paging_clause(-5, 20)
        assert params == (0, 20)

    def test_clamps_zero_limit_to_one(self):
        sql, params = paging_clause(0, 0)
        assert params == (0, 1)


class TestPagingSummary:
    def test_has_more_when_full_page(self):
        s = paging_summary(offset=0, limit=10, returned=10)
        assert s["has_more"] is True
        assert s["next_offset"] == 10
        assert s["offset"] == 0
        assert s["limit"] == 10
        assert s["returned"] == 10

    def test_no_more_when_partial_page(self):
        s = paging_summary(offset=20, limit=10, returned=3)
        assert s["has_more"] is False
        assert s["next_offset"] is None

    def test_no_more_when_empty_page(self):
        s = paging_summary(offset=100, limit=10, returned=0)
        assert s["has_more"] is False
        assert s["next_offset"] is None

    def test_next_offset_accumulates(self):
        s = paging_summary(offset=40, limit=10, returned=10)
        assert s["next_offset"] == 50


class TestShiftYear:
    def test_simple_back_one_year(self):
        assert shift_year("2026-04-14", -1) == "2025-04-14"

    def test_simple_forward_one_year(self):
        assert shift_year("2026-01-01", 1) == "2027-01-01"

    def test_leap_day_falls_back_to_feb_28(self):
        assert shift_year("2024-02-29", -1) == "2023-02-28"

    def test_leap_day_forward_falls_back_to_feb_28(self):
        assert shift_year("2024-02-29", 1) == "2025-02-28"

    def test_non_leap_day_unaffected(self):
        assert shift_year("2024-02-28", -1) == "2023-02-28"


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
        with pytest.raises(ValueError):
            subtract_months("not-a-date", 1)


class TestBuildFilterWhere:
    def test_none_filters_produces_empty(self):
        sql, params = build_filter_where(None, cs="cs", p="p", country_col="DeliveryCountry")
        assert sql == ""
        assert params == ()

    def test_empty_dict_filters_produces_empty(self):
        sql, params = build_filter_where({}, cs="cs", p="p", country_col="DeliveryCountry")
        assert sql == ""
        assert params == ()

    def test_country_fragment_and_params(self):
        sql, params = build_filter_where({"country": ["NL", "BE"]}, country_col="DeliveryCountry")
        assert sql == " AND DeliveryCountry IN (?,?)"
        assert params == ("NL", "BE")

    def test_business_unit_maps_to_segment_group_codes(self):
        sql, params = build_filter_where({"business_unit": ["B2B"]}, cs="cs")
        assert sql == " AND cs.CustSuppGroup IN (?,?,?,?,?)"
        assert params == ("G2", "G3", "G4", "G5", "G6")

    def test_business_unit_union_dedup_stable_order(self):
        sql, params = build_filter_where({"business_unit": ["B2C", "IC", "B2C"]}, cs="cs")
        assert sql == " AND cs.CustSuppGroup IN (?,?)"
        assert params == ("ND", "INTC")

    def test_customer_group_fragment_and_params(self):
        sql, params = build_filter_where({"customer_group": ["ND"]}, cs="cs")
        assert sql == " AND cs.CustSuppGroup IN (?)"
        assert params == ("ND",)

    def test_make_fragment_and_params(self):
        sql, params = build_filter_where({"make": ["DU"]}, p="p")
        assert sql == " AND p.Make IN (?)"
        assert params == ("DU",)

    def test_productgroup_fragment_and_params(self):
        sql, params = build_filter_where({"productgroup": ["ENG"]}, p="p")
        assert sql == " AND p.Productgroup IN (?)"
        assert params == ("ENG",)

    def test_missing_alias_skips_dim(self):
        sql, params = build_filter_where({"make": ["DU"]})
        assert sql == ""
        assert params == ()

    def test_missing_alias_skips_dim_but_others_still_apply(self):
        sql, params = build_filter_where(
            {"make": ["DU"], "country": ["NL"]}, country_col="DeliveryCountry"
        )
        assert sql == " AND DeliveryCountry IN (?)"
        assert params == ("NL",)

    def test_empty_value_list_skips_dim(self):
        sql, params = build_filter_where({"country": []}, country_col="DeliveryCountry")
        assert sql == ""
        assert params == ()

    def test_unknown_dim_skipped(self):
        sql, params = build_filter_where(
            {"bogus_dim": ["x"]}, cs="cs", p="p", country_col="DeliveryCountry"
        )
        assert sql == ""
        assert params == ()

    def test_multiple_dims_anded_with_combined_params(self):
        sql, params = build_filter_where(
            {"customer_group": ["ND"], "make": ["DU"], "country": ["NL", "BE"]},
            cs="cs",
            p="p",
            country_col="DeliveryCountry",
        )
        assert sql == (
            " AND cs.CustSuppGroup IN (?) AND p.Make IN (?) AND DeliveryCountry IN (?,?)"
        )
        assert params == ("ND", "DU", "NL", "BE")

    def test_params_are_tuple(self):
        sql, params = build_filter_where({"country": ["NL"]}, country_col="DeliveryCountry")
        assert isinstance(params, tuple)
