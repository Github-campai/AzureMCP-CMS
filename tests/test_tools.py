from unittest.mock import patch

import pytest

from admcms_mcp.tools import get_dead_stock, get_lookups, get_schema, query_database


class TestQueryDatabase:
    @patch("admcms_mcp.tools.execute_query")
    def test_returns_structured_result(self, mock_execute):
        mock_execute.return_value = [{"id": 1}, {"id": 2}]

        result = query_database("SELECT id FROM foo", max_rows=10)

        assert result["rows"] == [{"id": 1}, {"id": 2}]
        assert result["row_count"] == 2
        assert "sql" in result
        mock_execute.assert_called_once_with("SELECT id FROM foo", max_rows=10)

    @patch("admcms_mcp.tools.execute_query")
    def test_returns_error_for_invalid_sql(self, mock_execute):
        mock_execute.side_effect = ValueError("Query contains forbidden keyword: INSERT")

        result = query_database("INSERT INTO foo VALUES (1)")

        assert "error" in result
        assert "INSERT" in result["error"]
        assert result.get("rows") is None or result.get("rows") == []


class TestGetSchema:
    @patch("admcms_mcp.tools.get_schema_columns")
    def test_returns_column_info(self, mock_get_cols):
        mock_get_cols.return_value = [
            {"COLUMN_NAME": "id", "DATA_TYPE": "int", "IS_NULLABLE": "NO"},
        ]

        result = get_schema("ARTIKEL")

        assert result["table"] == "ARTIKEL"
        assert result["columns"] == [
            {"COLUMN_NAME": "id", "DATA_TYPE": "int", "IS_NULLABLE": "NO"},
        ]
        mock_get_cols.assert_called_once_with("ARTIKEL")

    @patch("admcms_mcp.tools.get_schema_columns")
    def test_returns_error_on_unknown_table(self, mock_get_cols):
        mock_get_cols.return_value = []

        result = get_schema("DOES_NOT_EXIST")

        assert "error" in result or result["columns"] == []


class TestGetLookups:
    @patch("admcms_mcp.tools.get_distinct_values")
    def test_returns_distinct_values(self, mock_get_distinct):
        mock_get_distinct.return_value = ["A", "B", "C"]

        result = get_lookups("ARTIKEL", "MERK", top_n=10)

        assert result["table"] == "ARTIKEL"
        assert result["column"] == "MERK"
        assert result["values"] == ["A", "B", "C"]
        mock_get_distinct.assert_called_once_with("ARTIKEL", "MERK", top_n=10)

    @patch("admcms_mcp.tools.get_distinct_values")
    def test_returns_error_on_invalid_identifier(self, mock_get_distinct):
        mock_get_distinct.side_effect = ValueError("Invalid table name")

        result = get_lookups("foo'; DROP--", "bar")

        assert "error" in result
        assert "Invalid" in result["error"]


class TestSearchProducts:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_matched_products(self, mock_execute):
        from admcms_mcp.tools import search_products

        mock_execute.return_value = [
            {
                "Productcode": "ABC123",
                "Searchname": "FILTER",
                "Make": "MAHLE",
                "Productgroup": "HOD",
                "Salesprice": 25.0,
                "InActiveProgram": False,
            }
        ]
        result = search_products("filter", limit=10)
        assert len(result["data"]) == 1
        assert result["data"][0]["Make"] == "MAHLE"
        assert result["summary"]["count"] == 1
        assert result["summary"]["search_term"] == "filter"
        assert result["summary"]["offset"] == 0
        assert result["summary"]["has_more"] is False
        assert result["error"] is None

    @patch("admcms_mcp.tools.execute_query_params")
    def test_wraps_and_escapes_search_term(self, mock_execute):
        from admcms_mcp.tools import search_products

        mock_execute.return_value = []
        search_products("50%", limit=5)
        called_sql = mock_execute.call_args[0][0]
        called_params = mock_execute.call_args[0][1]
        # Params should contain the wrapped + escaped pattern
        assert called_params[0] == "%50[%]%"
        assert "LIKE" in called_sql.upper()

    @patch("admcms_mcp.tools.execute_query_params")
    def test_clamps_limit_at_500(self, mock_execute):
        from admcms_mcp.tools import search_products

        mock_execute.return_value = []
        search_products("x", limit=99999)
        max_rows = mock_execute.call_args.kwargs.get("max_rows")
        assert max_rows == 500

    @patch("admcms_mcp.tools.execute_query_params")
    def test_empty_results(self, mock_execute):
        from admcms_mcp.tools import search_products

        mock_execute.return_value = []
        result = search_products("nothingmatches")
        assert result["data"] == []
        assert result["summary"]["count"] == 0
        assert result["error"] is None

    @patch("admcms_mcp.tools.execute_query_params")
    def test_exception_becomes_error(self, mock_execute):
        from admcms_mcp.tools import search_products

        mock_execute.side_effect = RuntimeError("DB down")
        result = search_products("filter")
        assert result["data"] == []
        assert "DB down" in result["error"]

    @patch("admcms_mcp.tools.execute_query_params")
    def test_paging_uses_offset_fetch(self, mock_execute):
        from admcms_mcp.tools import search_products

        mock_execute.return_value = []
        search_products("x", limit=25, offset=50)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY" in sql
        assert 50 in params  # offset
        assert 25 in params  # limit

    @patch("admcms_mcp.tools.execute_query_params")
    def test_has_more_when_page_is_full(self, mock_execute):
        from admcms_mcp.tools import search_products

        # Return exactly `limit` rows → has_more = True
        mock_execute.return_value = [{"Productcode": f"P{i}"} for i in range(5)]
        result = search_products("x", limit=5)
        assert result["summary"]["has_more"] is True
        assert result["summary"]["next_offset"] == 5


class TestGetRevenue:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_total_group_by(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"total_amount": 12345.67, "invoice_count": 50}]
        result = get_revenue("2026-03-01", "2026-04-01", group_by="total")
        assert result["error"] is None
        assert result["summary"]["total_amount"] == 12345.67
        assert result["summary"]["invoice_count"] == 50
        assert result["summary"]["period"] == "2026-03-01..2026-04-01"
        assert result["summary"]["group_by"] == "total"

    @patch("admcms_mcp.tools.execute_query_params")
    def test_month_group_by_returns_breakdown(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [
            {"period": "202603", "amount": 5000.0},
            {"period": "202604", "amount": 7000.0},
        ]
        result = get_revenue("2026-03-01", "2026-05-01", group_by="month")
        assert len(result["data"]) == 2
        assert result["summary"]["total_amount"] == 12000.0

    @patch("admcms_mcp.tools.execute_query_params")
    def test_invalid_group_by_rejected(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        result = get_revenue("2026-01-01", "2026-02-01", group_by="wrong_value")
        assert result["data"] == []
        assert "group_by" in result["error"]
        mock_execute.assert_not_called()

    @patch("admcms_mcp.tools.execute_query_params")
    def test_invalid_start_date_rejected(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        result = get_revenue("bad-date", "2026-02-01")
        assert result["data"] == []
        assert "date" in result["error"].lower()
        mock_execute.assert_not_called()

    @patch("admcms_mcp.tools.execute_query_params")
    def test_make_group_by_joins_product(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"Make": "MAHLE", "amount": 99.0}]
        get_revenue("2026-01-01", "2026-02-01", group_by="make")
        sql = mock_execute.call_args[0][0]
        assert "PRODUCT" in sql.upper()
        assert "Make" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_customer_group_by_joins_custsupplier(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"Searchname": "ACME", "amount": 99.0}]
        get_revenue("2026-01-01", "2026-02-01", group_by="customer")
        sql = mock_execute.call_args[0][0]
        assert "CUSTSUPPLIER" in sql.upper()


class TestGetRevenueCountryAndLY:
    @patch("admcms_mcp.tools._fetch_revenue")
    def test_country_group_by(self, mock_fetch):
        from admcms_mcp.tools import get_revenue

        mock_fetch.return_value = [
            {"DeliveryCountry": "DE", "amount": 1000.0},
            {"DeliveryCountry": "NL", "amount": 500.0},
        ]
        result = get_revenue("2026-01-01", "2026-02-01", group_by="country")
        assert result["error"] is None
        assert len(result["data"]) == 2
        assert result["summary"]["total_amount"] == 1500.0
        assert result["summary"]["group_by"] == "country"

    @patch("admcms_mcp.tools._fetch_revenue")
    def test_productgroup_group_by(self, mock_fetch):
        from admcms_mcp.tools import get_revenue

        mock_fetch.return_value = [{"Productgroup": "HOD", "amount": 2000.0}]
        result = get_revenue("2026-01-01", "2026-02-01", group_by="productgroup")
        assert result["data"][0]["Productgroup"] == "HOD"

    @patch("admcms_mcp.tools._fetch_revenue")
    def test_include_ly_total(self, mock_fetch):
        from admcms_mcp.tools import get_revenue

        # First call = current, second call = LY
        mock_fetch.side_effect = [
            [{"total_amount": 1200.0, "invoice_count": 30}],
            [{"total_amount": 1000.0, "invoice_count": 25}],
        ]
        result = get_revenue("2026-01-01", "2026-02-01", group_by="total", include_ly=True)
        row = result["data"][0]
        assert row["total_amount"] == 1200.0
        assert row["total_amount_ly"] == 1000.0
        assert row["delta_amount"] == 200.0
        assert abs(row["delta_pct"] - 20.0) < 0.001

    @patch("admcms_mcp.tools._fetch_revenue")
    def test_include_ly_per_country_merges_by_key(self, mock_fetch):
        from admcms_mcp.tools import get_revenue

        mock_fetch.side_effect = [
            [  # current
                {"DeliveryCountry": "DE", "amount": 1000.0},
                {"DeliveryCountry": "NL", "amount": 500.0},
            ],
            [  # LY
                {"DeliveryCountry": "DE", "amount": 800.0},
                {"DeliveryCountry": "FR", "amount": 200.0},  # existed LY only
            ],
        ]
        result = get_revenue("2026-01-01", "2026-02-01", group_by="country", include_ly=True)
        rows = {r["DeliveryCountry"]: r for r in result["data"]}
        assert rows["DE"]["amount"] == 1000.0
        assert rows["DE"]["amount_ly"] == 800.0
        assert rows["DE"]["delta"] == 200.0
        assert rows["NL"]["amount"] == 500.0
        assert rows["NL"]["amount_ly"] == 0.0  # new this year
        assert rows["FR"]["amount"] == 0.0  # disappeared
        assert rows["FR"]["amount_ly"] == 200.0
        assert rows["FR"]["delta_pct"] == -100.0

    @patch("admcms_mcp.tools._fetch_revenue")
    def test_month_ly_shifts_year_for_match(self, mock_fetch):
        from admcms_mcp.tools import get_revenue

        mock_fetch.side_effect = [
            [{"period": "202601", "amount": 100.0}],  # current Jan 2026
            [{"period": "202501", "amount": 80.0}],  # LY Jan 2025
        ]
        result = get_revenue("2026-01-01", "2026-02-01", group_by="month", include_ly=True)
        rows = {r["period"]: r for r in result["data"]}
        assert rows["202601"]["amount"] == 100.0
        assert rows["202601"]["amount_ly"] == 80.0
        assert rows["202601"]["delta"] == 20.0


class TestGetRevenueFilters:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_country_filter_adds_where_and_param(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"total_amount": 100.0, "invoice_count": 5}]
        get_revenue("2026-01-01", "2026-02-01", group_by="total", filters={"country": ["NL"]})
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "DeliveryCountry IN (?)" in sql
        assert "NL" in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_customer_group_filter_adds_custsupplier_join(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"total_amount": 100.0, "invoice_count": 5}]
        get_revenue(
            "2026-01-01",
            "2026-02-01",
            group_by="total",
            filters={"customer_group": ["G2"]},
        )
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert (
            "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer AND cs.CustSuppType = '1'" in sql
        )
        assert "cs.CustSuppGroup IN (?)" in sql
        assert "G2" in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_make_filter_ignored_for_non_line_level_group_by(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"total_amount": 100.0, "invoice_count": 5}]
        get_revenue("2026-01-01", "2026-02-01", group_by="total", filters={"make": ["MAHLE"]})
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "Make IN" not in sql
        assert params == ("20260101", "20260201")

    @patch("admcms_mcp.tools.execute_query_params")
    def test_make_filter_applies_on_make_group_by(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"Make": "MAHLE", "amount": 10.0}]
        get_revenue("2026-01-01", "2026-02-01", group_by="make", filters={"make": ["MAHLE"]})
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "p.Make IN (?)" in sql
        assert "MAHLE" in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_filters_none_regression_matches_baseline_sql(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"total_amount": 100.0, "invoice_count": 5}]
        get_revenue("2026-01-01", "2026-02-01", group_by="total", filters=None)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert sql == (
            "SELECT SUM(si.LineTotal) AS total_amount, COUNT(*) AS invoice_count "
            "FROM SALESINVOICE si "
            "WHERE si.InvoiceDate >= ? AND si.InvoiceDate < ?"
        )
        assert params == ("20260101", "20260201")


class TestGetRevenueByBusinessUnit:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_business_unit_breakdown(self, mock_execute):
        from admcms_mcp.tools import get_revenue_by_business_unit

        mock_execute.return_value = [
            {"business_unit": "B2C", "invoice_count": 5952, "total_amount": 537092.86},
            {"business_unit": "B2B", "invoice_count": 1242, "total_amount": 368158.93},
            {"business_unit": "IC", "invoice_count": 3821, "total_amount": 168499.84},
            {"business_unit": "Overig", "invoice_count": 65, "total_amount": 47249.70},
        ]
        result = get_revenue_by_business_unit("2026-04-01", "2026-05-01")
        assert result["error"] is None
        assert len(result["data"]) == 4
        assert result["summary"]["invoice_count"] == 5952 + 1242 + 3821 + 65
        assert abs(result["summary"]["total_amount"] - 1121001.33) < 0.01
        assert result["summary"]["period"] == "2026-04-01..2026-05-01"
        assert result["summary"]["business_units"] == 4

    @patch("admcms_mcp.tools.execute_query_params")
    def test_sql_classifies_via_custsuppgroup(self, mock_execute):
        from admcms_mcp.tools import get_revenue_by_business_unit

        mock_execute.return_value = []
        get_revenue_by_business_unit("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        assert "CustSuppGroup" in sql
        assert "'ND'" in sql and "'B2C'" in sql
        assert "'G2'" in sql and "'B2B'" in sql
        assert "'INTC'" in sql and "'IC'" in sql
        assert "'Overig'" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_uses_line_grain_turnover_and_invoicedate(self, mock_execute):
        from admcms_mcp.tools import get_revenue_by_business_unit

        mock_execute.return_value = []
        get_revenue_by_business_unit("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        # header grain (fast) — SUM(LineTotal), CustSuppType='1' guard
        assert "SUM(si.LineTotal)" in sql
        assert "FROM SALESINVOICE si" in sql
        assert "cs.CustSuppType = '1'" in sql
        assert "InvoiceDate" in sql
        assert params == ("20260101", "20260201")

    @patch("admcms_mcp.tools.execute_query_params")
    def test_invalid_date_rejected(self, mock_execute):
        from admcms_mcp.tools import get_revenue_by_business_unit

        result = get_revenue_by_business_unit("bad-date", "2026-02-01")
        assert result["data"] == []
        assert "date" in result["error"].lower()
        mock_execute.assert_not_called()

    @patch("admcms_mcp.tools.execute_query_params")
    def test_empty_results(self, mock_execute):
        from admcms_mcp.tools import get_revenue_by_business_unit

        mock_execute.return_value = []
        result = get_revenue_by_business_unit("2026-01-01", "2026-02-01")
        assert result["data"] == []
        assert result["summary"]["total_amount"] == 0
        assert result["summary"]["invoice_count"] == 0


class TestGetTopCustomers:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_top_customers_ordered(self, mock_execute):
        from admcms_mcp.tools import get_top_customers

        mock_execute.return_value = [
            {
                "CustSuppnr": 100,
                "Searchname": "ACME",
                "CustSuppGroup": "G3",
                "invoice_count": 50,
                "total_amount": 250000.0,
            },
            {
                "CustSuppnr": 200,
                "Searchname": "BETA",
                "CustSuppGroup": "G3",
                "invoice_count": 30,
                "total_amount": 150000.0,
            },
        ]
        result = get_top_customers("2026-01-01", "2026-02-01", limit=5)
        assert result["error"] is None
        assert len(result["data"]) == 2
        assert result["summary"]["top_total_amount"] == 400000.0
        assert result["summary"]["count"] == 2
        assert result["summary"]["period"] == "2026-01-01..2026-02-01"

    @patch("admcms_mcp.tools.execute_query_params")
    def test_filters_custsupptype_1(self, mock_execute):
        from admcms_mcp.tools import get_top_customers

        mock_execute.return_value = []
        get_top_customers("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        assert "CustSuppType = '1'" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_clamps_limit(self, mock_execute):
        from admcms_mcp.tools import get_top_customers

        mock_execute.return_value = []
        get_top_customers("2026-01-01", "2026-02-01", limit=99999)
        max_rows = mock_execute.call_args.kwargs.get("max_rows")
        assert max_rows == 500

    @patch("admcms_mcp.tools.execute_query_params")
    def test_invalid_date_rejected(self, mock_execute):
        from admcms_mcp.tools import get_top_customers

        result = get_top_customers("bad-date", "2026-02-01")
        assert result["data"] == []
        assert "date" in result["error"].lower()
        mock_execute.assert_not_called()


class TestGetTopCustomersFilters:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_country_filter(self, mock_execute):
        from admcms_mcp.tools import get_top_customers

        mock_execute.return_value = []
        get_top_customers("2026-01-01", "2026-02-01", filters={"country": ["NL"]})
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "si.DeliveryCountry IN (?)" in sql
        assert "NL" in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_filters_none_regression_matches_baseline_sql(self, mock_execute):
        from admcms_mcp.tools import get_top_customers

        mock_execute.return_value = []
        get_top_customers("2026-01-01", "2026-02-01", limit=5, filters=None)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert sql == (
            "SELECT TOP (5) cs.CustSuppnr, cs.Searchname, cs.CustSuppGroup, "
            "COUNT(DISTINCT si.Invoicenr) AS invoice_count, "
            "SUM(si.LineTotal) AS total_amount "
            "FROM SALESINVOICE si "
            "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer AND cs.CustSuppType = '1' "
            "WHERE si.InvoiceDate >= ? AND si.InvoiceDate < ? "
            "GROUP BY cs.CustSuppnr, cs.Searchname, cs.CustSuppGroup "
            "ORDER BY total_amount DESC"
        )
        assert params == ("20260101", "20260201")


class TestGetShippingSummary:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_shipping_totals(self, mock_execute):
        from admcms_mcp.tools import get_shipping_summary

        mock_execute.return_value = [
            {
                "invoice_count": 500,
                "total_ship_cost": 12500.0,
                "total_ship_cost_calculated": 14000.0,
                "total_order_costs": 300.0,
                "avg_ship_cost": 25.0,
            }
        ]
        result = get_shipping_summary("2026-03-01", "2026-04-01")
        assert result["error"] is None
        assert result["summary"]["invoice_count"] == 500
        assert result["summary"]["total_ship_cost"] == 12500.0
        assert result["summary"]["total_ship_cost_calculated"] == 14000.0
        assert result["summary"]["ship_cost_delta"] == -1500.0
        assert result["summary"]["total_order_costs"] == 300.0

    @patch("admcms_mcp.tools.execute_query_params")
    def test_empty_period_returns_zeros(self, mock_execute):
        from admcms_mcp.tools import get_shipping_summary

        mock_execute.return_value = [
            {
                "invoice_count": 0,
                "total_ship_cost": None,
                "total_ship_cost_calculated": None,
                "total_order_costs": None,
                "avg_ship_cost": None,
            }
        ]
        result = get_shipping_summary("2026-01-01", "2026-01-02")
        assert result["summary"]["invoice_count"] == 0
        assert result["summary"]["total_ship_cost"] == 0
        assert result["summary"]["ship_cost_delta"] == 0

    @patch("admcms_mcp.tools.execute_query_params")
    def test_sql_uses_shipcost_columns(self, mock_execute):
        from admcms_mcp.tools import get_shipping_summary

        mock_execute.return_value = []
        get_shipping_summary("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        assert "ShipCost" in sql
        assert "ShipCostCalculated" in sql
        assert "OrderCosts" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_invalid_date_rejected(self, mock_execute):
        from admcms_mcp.tools import get_shipping_summary

        result = get_shipping_summary("2026-01-01", "not-a-date")
        assert result["data"] == []
        assert "date" in result["error"].lower()
        mock_execute.assert_not_called()


class TestShippingSummaryFilters:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_default_excludes_credits_and_unpaid(self, mock_execute):
        from admcms_mcp.tools import get_shipping_summary

        mock_execute.return_value = [{"invoice_count": 100}]
        get_shipping_summary("2026-03-01", "2026-04-01")
        sql = mock_execute.call_args[0][0]
        assert "InvoiceType = '0'" in sql
        assert "FullyPaid = 1" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_flags_off_removes_scope_filters(self, mock_execute):
        from admcms_mcp.tools import get_shipping_summary

        mock_execute.return_value = [{"invoice_count": 100}]
        get_shipping_summary("2026-03-01", "2026-04-01", exclude_credits=False, paid_only=False)
        sql = mock_execute.call_args[0][0]
        assert "InvoiceType" not in sql
        assert "FullyPaid" not in sql


class TestGetShippingCostAnalysis:
    @patch("admcms_mcp.tools._fetch_shipping_analysis")
    def test_total_computes_margin(self, mock_fetch):
        from admcms_mcp.tools import get_shipping_cost_analysis

        mock_fetch.return_value = [
            {
                "order_count": 100,
                "total_charged": 1000.0,
                "avg_charged": 10.0,
                "total_carrier_cost": 800.0,
                "avg_carrier_cost": 8.0,
                "turnover": 10000.0,
            }
        ]
        result = get_shipping_cost_analysis("2026-03-01", "2026-04-01", group_by="total")
        row = result["data"][0]
        assert row["shipping_margin"] == 200.0
        assert row["avg_shipping_margin"] == 2.0
        assert row["ship_cost_pct_of_turnover"] == 10.0
        assert result["summary"]["shipping_margin"] == 200.0

    @patch("admcms_mcp.tools._fetch_shipping_analysis")
    def test_segment_carrier_group_by(self, mock_fetch):
        from admcms_mcp.tools import get_shipping_cost_analysis

        mock_fetch.return_value = [
            {
                "segment": "B2C",
                "carrier": "FedEx",
                "order_count": 50,
                "total_charged": 600.0,
                "avg_charged": 12.0,
                "total_carrier_cost": 500.0,
                "avg_carrier_cost": 10.0,
                "turnover": 5000.0,
            }
        ]
        result = get_shipping_cost_analysis("2026-03-01", "2026-04-01", group_by="segment_carrier")
        assert result["data"][0]["segment"] == "B2C"
        assert result["data"][0]["carrier"] == "FedEx"
        assert result["data"][0]["shipping_margin"] == 100.0

    @patch("admcms_mcp.tools._fetch_shipping_analysis")
    def test_include_ly_adds_delta_carrier_cost_pct(self, mock_fetch):
        from admcms_mcp.tools import get_shipping_cost_analysis

        mock_fetch.side_effect = [
            [  # current
                {
                    "segment": "B2C",
                    "carrier": "FedEx",
                    "order_count": 100,
                    "total_charged": 1200.0,
                    "avg_charged": 12.0,
                    "total_carrier_cost": 1100.0,
                    "avg_carrier_cost": 11.0,
                    "turnover": 10000.0,
                }
            ],
            [  # LY
                {
                    "segment": "B2C",
                    "carrier": "FedEx",
                    "order_count": 80,
                    "total_charged": 800.0,
                    "avg_charged": 10.0,
                    "total_carrier_cost": 640.0,
                    "avg_carrier_cost": 8.0,
                    "turnover": 8000.0,
                }
            ],
        ]
        result = get_shipping_cost_analysis(
            "2026-03-01",
            "2026-04-01",
            group_by="segment_carrier",
            include_ly=True,
        )
        row = result["data"][0]
        assert row["avg_carrier_cost_ly"] == 8.0
        assert row["delta_avg_carrier_cost"] == 3.0
        assert abs(row["delta_carrier_cost_pct"] - 37.5) < 0.01

    @patch("admcms_mcp.tools._fetch_shipping_analysis")
    def test_invalid_group_by_rejected(self, mock_fetch):
        from admcms_mcp.tools import get_shipping_cost_analysis

        result = get_shipping_cost_analysis("2026-03-01", "2026-04-01", group_by="bogus")
        assert result["data"] == []
        assert "group_by" in result["error"]
        mock_fetch.assert_not_called()

    @patch("admcms_mcp.tools._fetch_shipping_analysis")
    def test_invalid_date_rejected(self, mock_fetch):
        from admcms_mcp.tools import get_shipping_cost_analysis

        result = get_shipping_cost_analysis("bad", "2026-04-01")
        assert result["data"] == []
        assert "date" in result["error"].lower()
        mock_fetch.assert_not_called()


class TestDetectPriceAnomalies:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_anomalies_with_max(self, mock_execute):
        from admcms_mcp.tools import detect_price_anomalies

        mock_execute.return_value = [
            {
                "Invoicenr": 1234567,
                "Orderline": 5,
                "Productcode": "ABC",
                "qty": 1,
                "SalesPrice": 999998092651.0,
                "InvoiceAmountHC": 999998092651.0,
                "CostAmountBookedHC": 25.0,
                "InvoiceDate": "20260320",
                "Customer": 42,
            }
        ]
        result = detect_price_anomalies("2026-03-01", "2026-04-01")
        assert result["error"] is None
        assert len(result["data"]) == 1
        assert result["summary"]["count"] == 1
        assert result["summary"]["max_price_found"] == 999998092651.0
        assert result["summary"]["min_price_threshold"] == 10000.0

    @patch("admcms_mcp.tools.execute_query_params")
    def test_empty_result_means_no_anomalies(self, mock_execute):
        from admcms_mcp.tools import detect_price_anomalies

        mock_execute.return_value = []
        result = detect_price_anomalies("2026-01-01", "2026-02-01")
        assert result["data"] == []
        assert result["summary"]["count"] == 0
        assert result["summary"]["max_price_found"] == 0.0

    @patch("admcms_mcp.tools.execute_query_params")
    def test_sql_joins_salesinvoice_for_date_index(self, mock_execute):
        from admcms_mcp.tools import detect_price_anomalies

        mock_execute.return_value = []
        detect_price_anomalies("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        # Must join to Salesinvoice so InvoiceDate filter uses its index
        assert "JOIN Salesinvoice" in sql
        assert "InvoiceDate" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_checks_all_three_price_columns(self, mock_execute):
        from admcms_mcp.tools import detect_price_anomalies

        mock_execute.return_value = []
        detect_price_anomalies("2026-01-01", "2026-02-01", min_price=5000.0)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "SalesPrice > ?" in sql
        assert "InvoiceAmountHC > ?" in sql
        assert "CostAmountBookedHC > ?" in sql
        # Threshold appears 3 times in params (once per price column)
        assert params.count(5000.0) == 3

    @patch("admcms_mcp.tools.execute_query_params")
    def test_invalid_date_rejected(self, mock_execute):
        from admcms_mcp.tools import detect_price_anomalies

        result = detect_price_anomalies("bad", "2026-02-01")
        assert result["data"] == []
        assert "date" in result["error"].lower()
        mock_execute.assert_not_called()

    @patch("admcms_mcp.tools.execute_query_params")
    def test_negative_threshold_rejected(self, mock_execute):
        from admcms_mcp.tools import detect_price_anomalies

        result = detect_price_anomalies("2026-01-01", "2026-02-01", min_price=-1)
        assert result["data"] == []
        assert "min_price" in result["error"]
        mock_execute.assert_not_called()


class TestGetStockLevels:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_rows_and_totals(self, mock_execute):
        from admcms_mcp.tools import get_stock_levels

        mock_execute.return_value = [
            {
                "Productcode": "ABC",
                "Warehouse": "CM",
                "ActualStock": 10,
                "ActualStockValue": 250.0,
                "CostPrice": 25.0,
                "MinimumStock": 5,
                "MaximumStock": 50,
                "DefLocation": "A1",
            },
            {
                "Productcode": "ABC",
                "Warehouse": "DS",
                "ActualStock": 3,
                "ActualStockValue": 75.0,
                "CostPrice": 25.0,
                "MinimumStock": 0,
                "MaximumStock": 10,
                "DefLocation": "",
            },
        ]
        result = get_stock_levels(product_code="ABC")
        assert result["error"] is None
        assert len(result["data"]) == 2
        assert result["summary"]["total_qty"] == 13
        assert result["summary"]["total_value"] == 325.0
        assert result["summary"]["product_code"] == "ABC"

    @patch("admcms_mcp.tools.execute_query_params")
    def test_filter_product_and_warehouse(self, mock_execute):
        from admcms_mcp.tools import get_stock_levels

        mock_execute.return_value = []
        get_stock_levels(product_code="ABC", warehouse="CM", only_available=False)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "Productcode = ?" in sql
        assert "Warehouse = ?" in sql
        assert "ActualStock > 0" not in sql
        assert "ABC" in params
        assert "CM" in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_only_available_default(self, mock_execute):
        from admcms_mcp.tools import get_stock_levels

        mock_execute.return_value = []
        get_stock_levels()
        sql = mock_execute.call_args[0][0]
        assert "ActualStock > 0" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_clamps_limit(self, mock_execute):
        from admcms_mcp.tools import get_stock_levels

        mock_execute.return_value = []
        get_stock_levels(limit=99999)
        max_rows = mock_execute.call_args.kwargs.get("max_rows")
        assert max_rows == 500


class TestGetSupplierPurchases:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_top_suppliers(self, mock_execute):
        from admcms_mcp.tools import get_supplier_purchases

        mock_execute.return_value = [
            {
                "CustSuppnr": 100,
                "Searchname": "MAHLE",
                "invoice_count": 50,
                "total_goods": 500000.0,
                "total_vat": 105000.0,
                "total_other_costs": 2000.0,
            },
        ]
        result = get_supplier_purchases("2026-01-01", "2026-02-01", limit=5)
        assert result["error"] is None
        assert result["summary"]["top_total_goods"] == 500000.0
        assert result["summary"]["count"] == 1
        assert result["summary"]["period"] == "2026-01-01..2026-02-01"

    @patch("admcms_mcp.tools.execute_query_params")
    def test_excludes_corrupt_supplier_81_by_default(self, mock_execute):
        from admcms_mcp.tools import get_supplier_purchases

        mock_execute.return_value = []
        get_supplier_purchases("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "CustSuppnr NOT IN" in sql
        assert 81 in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_include_corrupt_flag_includes_all(self, mock_execute):
        from admcms_mcp.tools import get_supplier_purchases

        mock_execute.return_value = []
        get_supplier_purchases("2026-01-01", "2026-02-01", include_corrupt=True)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "CustSuppnr NOT IN" not in sql
        assert 81 not in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_filters_custsupptype_2(self, mock_execute):
        from admcms_mcp.tools import get_supplier_purchases

        mock_execute.return_value = []
        get_supplier_purchases("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        assert "CustSuppType = '2'" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_invalid_date_rejected(self, mock_execute):
        from admcms_mcp.tools import get_supplier_purchases

        result = get_supplier_purchases("bad", "2026-02-01")
        assert result["data"] == []
        assert "date" in result["error"].lower()
        mock_execute.assert_not_called()


class TestGetCustomerOrders:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_orders_with_summary(self, mock_execute):
        from admcms_mcp.tools import get_customer_orders

        mock_execute.return_value = [
            {
                "Ordernr": 1001,
                "Customer": 42,
                "Searchname": "ACME",
                "OrderDate": "20260410",
                "DeliveryDate": "20260415",
                "OrderAmount": 1500.0,
                "SalesOrdersystemStatus": "O",
            }
        ]
        result = get_customer_orders(customer_id=42)
        assert len(result["data"]) == 1
        assert result["summary"]["count"] == 1
        assert result["summary"]["total_order_value"] == 1500.0
        assert result["summary"]["customer_id"] == 42

    @patch("admcms_mcp.tools.execute_query_params")
    def test_no_customer_id_returns_all(self, mock_execute):
        from admcms_mcp.tools import get_customer_orders

        mock_execute.return_value = []
        get_customer_orders(customer_id=None)
        sql = mock_execute.call_args[0][0]
        assert "soh.Customer = ?" not in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_customer_id_included_in_filter(self, mock_execute):
        from admcms_mcp.tools import get_customer_orders

        mock_execute.return_value = []
        get_customer_orders(customer_id=42)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "soh.Customer = ?" in sql
        assert 42 in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_limit_clamped_at_500(self, mock_execute):
        from admcms_mcp.tools import get_customer_orders

        mock_execute.return_value = []
        get_customer_orders(customer_id=42, limit=99999)
        max_rows = mock_execute.call_args.kwargs.get("max_rows")
        assert max_rows == 500


class TestGetMarginReport:
    @patch("admcms_mcp.tools._fetch_margin")
    def test_total_computes_margin_and_pct(self, mock_fetch):
        from admcms_mcp.tools import get_margin_report

        mock_fetch.return_value = [{"turnover": 1000.0, "cost": 600.0}]
        result = get_margin_report("2026-01-01", "2026-02-01", group_by="total")
        row = result["data"][0]
        assert row["margin"] == 400.0
        assert row["margin_pct"] == 40.0
        assert result["summary"]["margin_pct"] == 40.0

    @patch("admcms_mcp.tools._fetch_margin")
    def test_country_breakdown_sorted(self, mock_fetch):
        from admcms_mcp.tools import get_margin_report

        mock_fetch.return_value = [
            {"DeliveryCountry": "DE", "turnover": 1000.0, "cost": 600.0},
            {"DeliveryCountry": "NL", "turnover": 500.0, "cost": 200.0},
        ]
        result = get_margin_report("2026-01-01", "2026-02-01", group_by="country")
        assert len(result["data"]) == 2
        assert result["data"][0]["margin"] == 400.0
        assert result["data"][1]["margin_pct"] == 60.0  # NL

    @patch("admcms_mcp.tools._fetch_margin")
    def test_include_ly_adds_delta_margin(self, mock_fetch):
        from admcms_mcp.tools import get_margin_report

        mock_fetch.side_effect = [
            [{"turnover": 1200.0, "cost": 700.0}],  # current: margin=500
            [{"turnover": 1000.0, "cost": 600.0}],  # LY: margin=400
        ]
        result = get_margin_report("2026-01-01", "2026-02-01", group_by="total", include_ly=True)
        row = result["data"][0]
        assert row["margin_ly"] == 400.0
        assert row["delta_margin"] == 100.0

    @patch("admcms_mcp.tools._fetch_margin")
    def test_invalid_group_by(self, mock_fetch):
        from admcms_mcp.tools import get_margin_report

        result = get_margin_report("2026-01-01", "2026-02-01", group_by="bogus")
        assert result["data"] == []
        assert "group_by" in result["error"]
        mock_fetch.assert_not_called()

    @patch("admcms_mcp.tools._fetch_margin")
    def test_invalid_date_rejected(self, mock_fetch):
        from admcms_mcp.tools import get_margin_report

        result = get_margin_report("bad-date", "2026-02-01")
        assert result["data"] == []
        assert "date" in result["error"].lower()
        mock_fetch.assert_not_called()

    @patch("admcms_mcp.tools.execute_query_params")
    def test_group_by_month_returns_period_rows_with_margin(self, mock_execute):
        from admcms_mcp.tools import get_margin_report

        mock_execute.return_value = [
            {"period": "202601", "turnover": 1000.0, "cost": 600.0},
            {"period": "202602", "turnover": 500.0, "cost": 300.0},
        ]
        res = get_margin_report("2026-01-01", "2026-03-01", group_by="month")
        assert res["error"] is None
        sql = mock_execute.call_args[0][0]
        assert "SUBSTRING(si.InvoiceDate, 1, 6) AS period" in sql
        assert "sil.PurchasedAt <> 81" in sql
        assert res["data"][0]["period"] == "202601"
        assert res["data"][0]["margin"] == 400.0
        assert round(res["data"][0]["margin_pct"], 1) == 40.0


class TestGetMarginReportFilters:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_business_unit_filter_maps_to_g_codes(self, mock_execute):
        from admcms_mcp.tools import get_margin_report

        mock_execute.return_value = [{"turnover": 100.0, "cost": 60.0}]
        get_margin_report(
            "2026-01-01",
            "2026-02-01",
            group_by="total",
            filters={"business_unit": ["B2B"]},
        )
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer" in sql
        assert "cs.CustSuppGroup IN (?,?,?,?,?)" in sql
        assert set(params[2:]) == {"G2", "G3", "G4", "G5", "G6"}

    @patch("admcms_mcp.tools.execute_query_params")
    def test_country_filter_on_total(self, mock_execute):
        from admcms_mcp.tools import get_margin_report

        mock_execute.return_value = [{"turnover": 100.0, "cost": 60.0}]
        get_margin_report(
            "2026-01-01",
            "2026-02-01",
            group_by="total",
            filters={"country": ["NL", "BE"]},
        )
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "si.DeliveryCountry IN (?,?)" in sql
        assert params[-2:] == ("NL", "BE")

    @patch("admcms_mcp.tools.execute_query_params")
    def test_filters_none_regression_matches_baseline_sql(self, mock_execute):
        from admcms_mcp.tools import get_margin_report

        mock_execute.return_value = [{"turnover": 100.0, "cost": 60.0}]
        get_margin_report("2026-01-01", "2026-02-01", group_by="total", filters=None)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert sql == (
            "SELECT SUM(sil.InvoiceAmountHC) AS turnover, "
            "SUM(sil.CostAmountBookedHC) AS cost "
            "FROM SalesinvoiceLines sil JOIN Salesinvoice si "
            "ON si.Invoicenr = sil.Invoicenr WHERE si.InvoiceDate >= ? "
            "AND si.InvoiceDate < ? AND sil.PurchasedAt <> 81"
        )
        assert params == ("20260101", "20260201")


class TestGetKpiSummary:
    @patch("admcms_mcp.tools._fetch_kpi_block")
    def test_basic_without_ly(self, mock_fetch):
        from admcms_mcp.tools import get_kpi_summary

        mock_fetch.return_value = {
            "turnover": 1000.0,
            "turnover_incl_shipping": 1100.0,
            "invoice_count": 25,
            "ordering_customers": 10,
            "aov": 40.0,
            "shipping_revenue": 100.0,
            "shipping_cost": 80.0,
            "shipping_margin": 20.0,
            "shipping_margin_pct": 20.0,
            "line_count": 60,
            "item_count": 150,
        }
        result = get_kpi_summary("2026-01-01", "2026-02-01", include_ly=False)
        assert result["error"] is None
        assert result["data"][0]["turnover"] == 1000.0
        assert result["data"][0]["aov"] == 40.0
        # No _ly fields when include_ly=False
        assert "turnover_ly" not in result["data"][0]

    @patch("admcms_mcp.tools._fetch_kpi_block")
    def test_with_ly_adds_deltas(self, mock_fetch):
        from admcms_mcp.tools import get_kpi_summary

        mock_fetch.side_effect = [
            {
                "turnover": 1200.0,
                "turnover_incl_shipping": 1300.0,
                "invoice_count": 30,
                "ordering_customers": 12,
                "aov": 40.0,
                "shipping_revenue": 100.0,
                "shipping_cost": 80.0,
                "shipping_margin": 20.0,
                "shipping_margin_pct": 20.0,
                "line_count": 70,
                "item_count": 160,
            },
            {
                "turnover": 1000.0,
                "turnover_incl_shipping": 1100.0,
                "invoice_count": 25,
                "ordering_customers": 10,
                "aov": 40.0,
                "shipping_revenue": 90.0,
                "shipping_cost": 70.0,
                "shipping_margin": 20.0,
                "shipping_margin_pct": 22.22,
                "line_count": 60,
                "item_count": 150,
            },
        ]
        result = get_kpi_summary("2026-01-01", "2026-02-01", include_ly=True)
        row = result["data"][0]
        assert row["turnover_ly"] == 1000.0
        assert row["turnover_delta"] == 200.0
        assert abs(row["turnover_delta_pct"] - 20.0) < 0.001
        assert row["ordering_customers_delta"] == 2


class TestGetKpiSummaryFilters:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_country_filter_applies_to_both_queries(self, mock_execute):
        from admcms_mcp.tools import get_kpi_summary

        mock_execute.return_value = [{}]
        get_kpi_summary("2026-01-01", "2026-02-01", include_ly=False, filters={"country": ["NL"]})
        # header-only (fast) — single query now
        header_sql = mock_execute.call_args_list[0][0][0]
        header_params = mock_execute.call_args_list[0][0][1]
        assert "DeliveryCountry IN (?)" in header_sql
        assert "NL" in header_params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_filters_none_regression_matches_baseline_sql(self, mock_execute):
        from admcms_mcp.tools import get_kpi_summary

        mock_execute.return_value = [{}]
        get_kpi_summary("2026-01-01", "2026-02-01", include_ly=False, filters=None)
        header_sql = mock_execute.call_args_list[0][0][0]
        header_params = mock_execute.call_args_list[0][0][1]
        assert header_sql == (
            "SELECT SUM(LineTotal) AS turnover, "
            "SUM(LineTotal + ShipCost) AS turnover_incl_shipping, "
            "COUNT(DISTINCT Invoicenr) AS invoice_count, "
            "COUNT(DISTINCT Customer) AS ordering_customers, "
            "SUM(ShipCost) AS shipping_revenue, "
            "SUM(ShipCostCalculated) AS shipping_cost, "
            "SUM(LastLineNumber) AS line_count "
            "FROM Salesinvoice "
            "WHERE InvoiceDate >= ? AND InvoiceDate < ?"
        )
        assert header_params == ("20260101", "20260201")
        # get_kpi_summary is now header-only (one query, no SalesinvoiceLines scan)
        assert mock_execute.call_count == 1


class TestGetCreditNoteAnalysis:
    @patch("admcms_mcp.tools._fetch_credit_notes")
    def test_segment_with_positive_credit_anomaly(self, mock_fetch):
        from admcms_mcp.tools import get_credit_note_analysis

        mock_fetch.return_value = [
            {
                "segment": "B2C",
                "credit_count": 10,
                "total_credit_amount": -500.0,
                "avg_credit_amount": -50.0,
                "positive_credit_count": 0,
            },
            {
                "segment": "Overig",
                "credit_count": 2,
                "total_credit_amount": 1000.0,
                "avg_credit_amount": 500.0,
                "positive_credit_count": 2,
            },
        ]
        result = get_credit_note_analysis("2026-04-01", "2026-05-01", group_by="segment")
        assert result["summary"]["positive_credit_count_total"] == 2
        assert result["summary"]["total_credit_count"] == 12

    @patch("admcms_mcp.tools._fetch_credit_notes")
    def test_invalid_group_by(self, mock_fetch):
        from admcms_mcp.tools import get_credit_note_analysis

        result = get_credit_note_analysis("2026-04-01", "2026-05-01", group_by="wrong")
        assert "group_by" in result["error"]
        mock_fetch.assert_not_called()


class TestGetPaymentMethodStats:
    @patch("admcms_mcp.tools._fetch_payment_methods")
    def test_enriches_with_pct(self, mock_fetch):
        from admcms_mcp.tools import get_payment_method_stats

        mock_fetch.return_value = [
            {
                "PaymentCondition": "PREP",
                "invoice_count": 800,
                "total_amount": 80000.0,
                "avg_amount": 100.0,
                "avg_payment_days": 0.0,
                "fully_paid_count": 800,
            },
            {
                "PaymentCondition": "AC",
                "invoice_count": 200,
                "total_amount": 40000.0,
                "avg_amount": 200.0,
                "avg_payment_days": 27.5,
                "fully_paid_count": 180,
            },
        ]
        result = get_payment_method_stats("2026-04-01", "2026-05-01")
        prep = next(r for r in result["data"] if r["PaymentCondition"] == "PREP")
        ac = next(r for r in result["data"] if r["PaymentCondition"] == "AC")
        assert prep["pct_of_total_count"] == 80.0
        assert prep["pct_fullypaid"] == 100.0
        assert ac["pct_of_total_count"] == 20.0
        assert ac["pct_fullypaid"] == 90.0


class TestGetSalesrepPerformance:
    @patch("admcms_mcp.tools._fetch_salesrep")
    def test_returns_rows(self, mock_fetch):
        from admcms_mcp.tools import get_salesrep_performance

        mock_fetch.return_value = [
            {
                "Salesrep": "WU",
                "invoice_count": 500,
                "unique_customers": 100,
                "total_turnover": 50000.0,
                "avg_order_value": 100.0,
            },
        ]
        result = get_salesrep_performance("2026-04-01", "2026-05-01")
        assert result["data"][0]["Salesrep"] == "WU"
        assert result["summary"]["total_turnover"] == 50000.0


class TestGetDiscountAnalysis:
    @patch("admcms_mcp.tools._fetch_discount")
    def test_computes_gross_and_pct(self, mock_fetch):
        from admcms_mcp.tools import get_discount_analysis

        mock_fetch.return_value = [
            {
                "segment": "B2C",
                "line_count": 100,
                "net_turnover": 900.0,
                "total_discount": 100.0,
                "avg_discount": 1.0,
            },
        ]
        result = get_discount_analysis("2026-04-01", "2026-05-01")
        row = result["data"][0]
        assert row["gross_turnover"] == 1000.0
        assert row["discount_pct"] == 10.0

    @patch("admcms_mcp.tools._fetch_discount")
    def test_invalid_group_by(self, mock_fetch):
        from admcms_mcp.tools import get_discount_analysis

        result = get_discount_analysis("2026-04-01", "2026-05-01", group_by="bogus")
        assert "group_by" in result["error"]
        mock_fetch.assert_not_called()


class TestGetDiscountAnalysisFilters:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_make_filter_adds_product_join(self, mock_execute):
        from admcms_mcp.tools import get_discount_analysis

        mock_execute.return_value = []
        get_discount_analysis(
            "2026-04-01", "2026-05-01", group_by="segment", filters={"make": ["MAHLE"]}
        )
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "JOIN PRODUCT p ON p.Productcode = sil.Productcode" in sql
        assert "p.Make IN (?)" in sql
        assert "MAHLE" in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_filters_none_regression_matches_baseline_sql(self, mock_execute):
        from admcms_mcp.tools import get_discount_analysis

        mock_execute.return_value = []
        get_discount_analysis("2026-04-01", "2026-05-01", group_by="total", filters=None)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert sql == (
            "SELECT COUNT(*) AS line_count, "
            "SUM(sil.InvoiceAmountHC) AS net_turnover, "
            "SUM(sil.DiscountAmount) AS total_discount, "
            "AVG(sil.DiscountAmount) AS avg_discount "
            "FROM SalesinvoiceLines sil JOIN Salesinvoice si "
            "ON si.Invoicenr = sil.Invoicenr "
            "WHERE si.InvoiceType = '0' "
            "AND si.InvoiceDate >= ? AND si.InvoiceDate < ? "
            "AND sil.DiscountAmount > 0 "
            "AND sil.PurchasedAt <> 81"
        )
        assert params == ("20260401", "20260501")


class TestGetOrderOriginMix:
    @patch("admcms_mcp.tools._fetch_order_origin")
    def test_returns_mix_with_pct(self, mock_fetch):
        from admcms_mcp.tools import get_order_origin_mix

        mock_fetch.return_value = [
            {"OrderOrigin": "06", "line_count": 880, "invoice_count": 80, "total_amount": 80000.0},
            {"OrderOrigin": "12", "line_count": 120, "invoice_count": 20, "total_amount": 10000.0},
        ]
        result = get_order_origin_mix("2026-04-01", "2026-05-01")
        web = next(r for r in result["data"] if r["OrderOrigin"] == "06")
        assert web["pct_of_total_lines"] == 88.0

    @patch("admcms_mcp.tools._fetch_order_origin")
    def test_include_ly_adds_delta(self, mock_fetch):
        from admcms_mcp.tools import get_order_origin_mix

        mock_fetch.side_effect = [
            [
                {
                    "OrderOrigin": "06",
                    "line_count": 1000,
                    "invoice_count": 100,
                    "total_amount": 100000.0,
                }
            ],
            [
                {
                    "OrderOrigin": "06",
                    "line_count": 800,
                    "invoice_count": 80,
                    "total_amount": 80000.0,
                }
            ],
        ]
        result = get_order_origin_mix("2026-04-01", "2026-05-01", include_ly=True)
        row = result["data"][0]
        assert row["line_count_ly"] == 800
        assert row["delta_line_count"] == 200
        assert row["delta_pct"] == 25.0


class TestGetOrderOriginMixFilters:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_business_unit_filter_adds_custsupplier_join(self, mock_execute):
        from admcms_mcp.tools import get_order_origin_mix

        mock_execute.return_value = []
        get_order_origin_mix("2026-04-01", "2026-05-01", filters={"business_unit": ["B2C"]})
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer" in sql
        assert "cs.CustSuppGroup IN (?)" in sql
        assert "ND" in params

    @patch("admcms_mcp.tools.execute_query_params")
    def test_filters_none_regression_matches_baseline_sql(self, mock_execute):
        from admcms_mcp.tools import get_order_origin_mix

        mock_execute.return_value = []
        get_order_origin_mix("2026-04-01", "2026-05-01", filters=None)
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert sql == (
            "SELECT sil.OrderOrigin, "
            "COUNT(*) AS line_count, "
            "COUNT(DISTINCT sil.Invoicenr) AS invoice_count, "
            "SUM(sil.InvoiceAmountHC) AS total_amount "
            "FROM SalesinvoiceLines sil "
            "JOIN Salesinvoice si ON si.Invoicenr = sil.Invoicenr "
            "WHERE si.InvoiceType = '0' "
            "AND si.InvoiceDate >= ? AND si.InvoiceDate < ? "
            "AND sil.PurchasedAt <> 81 "
            "GROUP BY sil.OrderOrigin ORDER BY line_count DESC"
        )
        assert params == ("20260401", "20260501")


class TestGetDeadStockValidation:
    def test_invalid_group_by_returns_error_response(self):
        result = get_dead_stock(group_by="bogus")
        assert result["data"] == []
        assert result["error"] is not None
        assert "group_by" in result["error"]

    @patch("admcms_mcp.tools.execute_query_params")
    def test_clamps_months_without_sale_high(self, mock_execute):
        mock_execute.side_effect = [
            [
                {
                    "total_dead_value": 0,
                    "total_dead_count": 0,
                    "never_sold_value": 0,
                    "never_sold_count": 0,
                }
            ],
            [],
        ]
        result = get_dead_stock(months_without_sale=999)
        assert result["summary"]["months_without_sale"] == 60

    @patch("admcms_mcp.tools.execute_query_params")
    def test_clamps_months_without_sale_low(self, mock_execute):
        mock_execute.side_effect = [
            [
                {
                    "total_dead_value": 0,
                    "total_dead_count": 0,
                    "never_sold_value": 0,
                    "never_sold_count": 0,
                }
            ],
            [],
        ]
        result = get_dead_stock(months_without_sale=0)
        assert result["summary"]["months_without_sale"] == 1

    @patch("admcms_mcp.tools.execute_query_params")
    def test_clamps_negative_min_stock_value(self, mock_execute):
        mock_execute.side_effect = [
            [
                {
                    "total_dead_value": 0,
                    "total_dead_count": 0,
                    "never_sold_value": 0,
                    "never_sold_count": 0,
                }
            ],
            [],
        ]
        result = get_dead_stock(min_stock_value=-50.0)
        assert result["summary"]["min_stock_value"] == 0.0


class TestGetDeadStockPerProduct:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_dead_products_with_expected_shape(self, mock_execute):
        # Two calls: 1) summary query, 2) paged data query
        mock_execute.side_effect = [
            [
                {
                    "total_dead_value": 5000.0,
                    "total_dead_count": 12,
                    "never_sold_value": 250.0,
                    "never_sold_count": 3,
                }
            ],
            [
                {
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
                }
            ],
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
            [
                {
                    "total_dead_value": 0,
                    "total_dead_count": 0,
                    "never_sold_value": 0,
                    "never_sold_count": 0,
                }
            ],
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
            [
                {
                    "total_dead_value": 0,
                    "total_dead_count": 0,
                    "never_sold_value": 0,
                    "never_sold_count": 0,
                }
            ],
            [],
        ]
        get_dead_stock(include_inactive_warehouses=True)
        paged_sql = mock_execute.call_args_list[1][0][0]
        assert "Warehouse NOT IN" not in paged_sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_paging_summary_reflects_full_page(self, mock_execute):
        mock_execute.side_effect = [
            [
                {
                    "total_dead_value": 0,
                    "total_dead_count": 0,
                    "never_sold_value": 0,
                    "never_sold_count": 0,
                }
            ],
            [
                {
                    "Productcode": f"P{i}",
                    "Description": "x",
                    "Make": "x",
                    "Productgroup": "x",
                    "Warehouse": "CM",
                    "ActualStock": 1,
                    "ActualStockValue": 1.0,
                    "CostPrice": 1.0,
                    "LastSaleDate": "2024-01-01",
                    "days_since_sale": 800,
                }
                for i in range(5)
            ],
        ]
        result = get_dead_stock(limit=5)
        assert result["summary"]["has_more"] is True
        assert result["summary"]["next_offset"] == 5

    @patch("admcms_mcp.tools.execute_query_params")
    def test_cutoff_date_is_today_minus_months(self, mock_execute):
        import datetime as _dt

        from admcms_mcp.query_helpers import subtract_months

        mock_execute.side_effect = [
            [
                {
                    "total_dead_value": 0,
                    "total_dead_count": 0,
                    "never_sold_value": 0,
                    "never_sold_count": 0,
                }
            ],
            [],
        ]
        result = get_dead_stock(months_without_sale=6)
        expected_cutoff = subtract_months(_dt.date.today().isoformat(), 6)
        assert result["summary"]["cutoff_date"] == expected_cutoff
        # And the same value should have been sent to SQL as a param
        # The cutoff_date param is shared across both queries; pick either call.
        paged_params = mock_execute.call_args_list[1][0][1]
        assert expected_cutoff in paged_params


class TestGetDeadStockAggregated:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_group_by_make_returns_aggregated_shape(self, mock_execute):
        mock_execute.side_effect = [
            [
                {
                    "total_dead_value": 100000.0,
                    "total_dead_count": 250,
                    "never_sold_value": 5000.0,
                    "never_sold_count": 20,
                }
            ],
            [
                {
                    "Make": "MAHLE",
                    "dead_sku_count": 38,
                    "total_stock_value": 24510.0,
                    "avg_months_since_sale": 19.4,
                    "oldest_last_sale": "2022-08-03",
                },
                {
                    "Make": "BOSCH",
                    "dead_sku_count": 22,
                    "total_stock_value": 14200.0,
                    "avg_months_since_sale": 14.1,
                    "oldest_last_sale": "2023-01-15",
                },
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
            [
                {
                    "total_dead_value": 0,
                    "total_dead_count": 0,
                    "never_sold_value": 0,
                    "never_sold_count": 0,
                }
            ],
            [
                {
                    "Productgroup": "HOD",
                    "dead_sku_count": 10,
                    "total_stock_value": 500.0,
                    "avg_months_since_sale": 16.0,
                    "oldest_last_sale": "2023-05-01",
                }
            ],
        ]
        result = get_dead_stock(group_by="productgroup")
        assert result["data"][0]["Productgroup"] == "HOD"
        # And the SQL should GROUP BY p.Productgroup, not p.Make
        paged_sql = mock_execute.call_args_list[1][0][0]
        assert "GROUP BY p.Productgroup" in paged_sql
        assert "p.Make" not in paged_sql


class TestGetDeadStockErrorHandling:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_db_exception_returns_error_response(self, mock_execute):
        mock_execute.side_effect = Exception("DB down")
        result = get_dead_stock()
        assert result["data"] == []
        assert result["error"] is not None
        assert "DB down" in result["error"]


class TestGetFilterOptions:
    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        # The in-process cache is module-global; isolate every case.
        from admcms_mcp.tools import _reset_filter_options_cache

        _reset_filter_options_cache()
        yield
        _reset_filter_options_cache()

    @patch("admcms_mcp.tools.execute_query_params")
    def test_returns_five_keys_with_distinct_values(self, mock_execute):
        from admcms_mcp.tools import get_filter_options

        mock_execute.side_effect = [
            [{"CustSuppGroup": "ND"}, {"CustSuppGroup": "G2"}],
            [{"DeliveryCountry": "NL"}, {"DeliveryCountry": "BE"}],
            [{"Make": "MAHLE"}],
            [{"Productgroup": "HOD"}],
        ]
        result = get_filter_options()
        assert set(result.keys()) == {
            "customer_group",
            "business_unit",
            "country",
            "make",
            "productgroup",
        }
        assert result["customer_group"] == ["ND", "G2"]
        assert result["business_unit"] == ["B2C", "B2B", "IC"]
        assert result["country"] == ["NL", "BE"]
        assert result["make"] == ["MAHLE"]
        assert result["productgroup"] == ["HOD"]

    @patch("admcms_mcp.tools.execute_query_params")
    def test_customer_group_query_scopes_to_real_customers(self, mock_execute):
        from admcms_mcp.tools import get_filter_options

        mock_execute.return_value = []
        get_filter_options()
        cg_sql = mock_execute.call_args_list[0][0][0]
        assert "CUSTSUPPLIER" in cg_sql
        assert "CustSuppType = '1'" in cg_sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_exception_returns_defaults_plus_error(self, mock_execute):
        from admcms_mcp.tools import get_filter_options

        mock_execute.side_effect = RuntimeError("DB down")
        result = get_filter_options()
        assert result["customer_group"] == []
        assert result["business_unit"] == ["B2C", "B2B", "IC"]
        assert result["country"] == []
        assert result["make"] == []
        assert result["productgroup"] == []
        assert "DB down" in result["error"]

    @patch("admcms_mcp.tools.execute_query_params")
    def test_one_slow_dim_does_not_blank_the_others(self, mock_execute):
        # make (3rd query) times out; customer_group/country/productgroup must survive.
        from admcms_mcp.tools import get_filter_options

        mock_execute.side_effect = [
            [{"CustSuppGroup": "ND"}],
            [{"DeliveryCountry": "NL"}],
            TimeoutError("Query timeout expired"),
            [{"Productgroup": "HOD"}],
        ]
        result = get_filter_options()
        assert result["customer_group"] == ["ND"]
        assert result["country"] == ["NL"]
        assert result["productgroup"] == ["HOD"]
        assert result["make"] == []
        assert "make" in result["error"]

    @patch("admcms_mcp.tools.execute_query_params")
    def test_successful_result_is_cached_no_second_db_hit(self, mock_execute):
        from admcms_mcp.tools import get_filter_options

        mock_execute.side_effect = [
            [{"CustSuppGroup": "ND"}],
            [{"DeliveryCountry": "NL"}],
            [{"Make": "MAHLE"}],
            [{"Productgroup": "HOD"}],
        ]
        first = get_filter_options()
        second = get_filter_options()
        # Second call is served from cache: no further DB calls beyond the first 4.
        assert mock_execute.call_count == 4
        assert second["make"] == first["make"] == ["MAHLE"]
        assert "error" not in second

    @patch("admcms_mcp.tools.execute_query_params")
    def test_partial_result_is_not_cached_fresh_and_merges_prior_good(self, mock_execute):
        from admcms_mcp.tools import get_filter_options

        # Call 1: everything ok EXCEPT productgroup -> partial, so NOT cached fresh.
        # Call 2: make fails, productgroup recovers -> make must retain call-1 value.
        mock_execute.side_effect = [
            [{"CustSuppGroup": "ND"}],
            [{"DeliveryCountry": "NL"}],
            [{"Make": "MAHLE"}],
            RuntimeError("boom"),
            [{"CustSuppGroup": "ND"}],
            [{"DeliveryCountry": "NL"}],
            RuntimeError("timeout"),
            [{"Productgroup": "HOD"}],
        ]
        first = get_filter_options()
        assert first["make"] == ["MAHLE"]
        assert "productgroup" in first["error"]

        second = get_filter_options()
        # Because call 1 was partial (ts stale), call 2 re-queries (8 total DB calls).
        assert mock_execute.call_count == 8
        assert second["make"] == ["MAHLE"]  # merged from prior known-good
        assert second["productgroup"] == ["HOD"]  # recovered this round
        assert "make" in second["error"]


# =========================================================================
# Fix slice: line-grain turnover excl. Supplier #81 (VDM PART) + customer-side
# CUSTSUPPLIER joins scoped to CustSuppType='1' + optional exclude_credits.
# =========================================================================


class TestLineGrainTurnoverExcludesSupplier81:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_get_revenue_make_excludes_81_total_is_header_grain(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        # make = line grain → excludes #81
        mock_execute.return_value = [{"Make": "DU", "amount": 100.0}]
        get_revenue("2026-01-01", "2026-02-01", group_by="make", filters=None)
        make_sql = mock_execute.call_args[0][0]
        assert "sil.PurchasedAt <> 81" in make_sql
        assert "SUM(sil.InvoiceAmountHC)" in make_sql

        # total = header grain (fast) → LineTotal, no line scan / no #81 clause
        mock_execute.return_value = [{"total_amount": 100.0, "invoice_count": 5}]
        get_revenue("2026-01-01", "2026-02-01", group_by="total", filters=None)
        total_sql = mock_execute.call_args[0][0]
        assert "SUM(si.LineTotal)" in total_sql
        assert "PurchasedAt" not in total_sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_fetch_margin_sql_excludes_supplier_81(self, mock_execute):
        from admcms_mcp.tools import _fetch_margin

        mock_execute.return_value = [{"turnover": 100.0, "cost": 60.0}]
        _fetch_margin("2026-01-01", "2026-02-01", "total")
        sql = mock_execute.call_args[0][0]
        assert "sil.PurchasedAt <> 81" in sql


class TestCountrySegmentMatrix:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_merges_line_and_shipping_by_country_segment(self, mock_execute):
        from admcms_mcp.tools import get_country_segment_matrix

        # 1st call = line grain (turnover/cost), 2nd call = header shipping
        mock_execute.side_effect = [
            [{"country": "NL", "segment": "B2B", "turnover": 1000.0, "cost": 600.0}],
            [{"country": "NL", "segment": "B2B", "shipping": 50.0, "shipping_cost": 30.0}],
        ]
        res = get_country_segment_matrix("2026-01-01", "2026-02-01")
        row = res["data"][0]
        assert row["country"] == "NL" and row["segment"] == "B2B"
        assert row["turnover"] == 1000.0
        assert row["margin"] == 400.0
        assert round(row["margin_pct"], 2) == 40.0
        assert row["shipping"] == 50.0
        assert row["shipping_cost"] == 30.0
        assert row["shipping_margin"] == 20.0
        assert round(row["shipping_margin_pct"], 2) == 40.0

    @patch("admcms_mcp.tools.execute_query_params")
    def test_line_query_excludes_81_and_guards_custsupptype(self, mock_execute):
        from admcms_mcp.tools import get_country_segment_matrix

        mock_execute.side_effect = [[], []]
        get_country_segment_matrix("2026-01-01", "2026-02-01")
        line_sql = mock_execute.call_args_list[0][0][0]
        assert "sil.PurchasedAt <> 81" in line_sql
        assert "cs.CustSuppType = '1'" in line_sql
        assert "SUM(sil.InvoiceAmountHC)" in line_sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_missing_shipping_row_defaults_to_zero(self, mock_execute):
        from admcms_mcp.tools import get_country_segment_matrix

        mock_execute.side_effect = [
            [{"country": "BE", "segment": "B2C", "turnover": 200.0, "cost": 150.0}],
            [],  # no shipping rows
        ]
        res = get_country_segment_matrix("2026-01-01", "2026-02-01")
        row = res["data"][0]
        assert row["shipping"] == 0 and row["shipping_margin"] == 0
        assert row["shipping_margin_pct"] is None


class TestCustSuppTypeGuardOnCustomerJoins:
    """Every customer-side CUSTSUPPLIER join must carry CustSuppType = '1'
    so customer numbers that also exist as supplier numbers aren't
    double-counted."""

    @patch("admcms_mcp.tools.execute_query_params")
    def test_business_unit_join_carries_custsupptype(self, mock_execute):
        from admcms_mcp.tools import get_revenue_by_business_unit

        mock_execute.return_value = []
        get_revenue_by_business_unit("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        assert (
            "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer AND cs.CustSuppType = '1'" in sql
        )

    @patch("admcms_mcp.tools.execute_query_params")
    def test_top_customers_join_carries_custsupptype(self, mock_execute):
        from admcms_mcp.tools import get_top_customers

        mock_execute.return_value = []
        get_top_customers("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        assert (
            "JOIN CUSTSUPPLIER cs ON cs.CustSuppnr = si.Customer AND cs.CustSuppType = '1'" in sql
        )

    @patch("admcms_mcp.tools.execute_query_params")
    def test_margin_segment_join_carries_custsupptype(self, mock_execute):
        from admcms_mcp.tools import _fetch_margin

        mock_execute.return_value = []
        _fetch_margin("2026-01-01", "2026-02-01", "segment")
        sql = mock_execute.call_args[0][0]
        assert "cs.CustSuppnr = si.Customer" in sql
        assert "cs.CustSuppType = '1'" in sql


class TestExcludeCreditsFlag:
    @patch("admcms_mcp.tools.execute_query_params")
    def test_get_revenue_exclude_credits_true_adds_invoicetype_filter(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"total_amount": 100.0, "invoice_count": 5}]
        get_revenue("2026-01-01", "2026-02-01", group_by="total", exclude_credits=True)
        sql = mock_execute.call_args[0][0]
        assert "InvoiceType = '0'" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_get_revenue_exclude_credits_false_default_omits_filter(self, mock_execute):
        from admcms_mcp.tools import get_revenue

        mock_execute.return_value = [{"total_amount": 100.0, "invoice_count": 5}]
        get_revenue("2026-01-01", "2026-02-01", group_by="total")
        sql = mock_execute.call_args[0][0]
        assert "InvoiceType" not in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_get_margin_report_exclude_credits_true_adds_invoicetype_filter(self, mock_execute):
        from admcms_mcp.tools import get_margin_report

        mock_execute.return_value = [{"turnover": 100.0, "cost": 60.0}]
        get_margin_report("2026-01-01", "2026-02-01", group_by="total", exclude_credits=True)
        sql = mock_execute.call_args[0][0]
        assert "si.InvoiceType = '0'" in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_get_margin_report_exclude_credits_false_default_omits_filter(self, mock_execute):
        from admcms_mcp.tools import get_margin_report

        mock_execute.return_value = [{"turnover": 100.0, "cost": 60.0}]
        get_margin_report("2026-01-01", "2026-02-01", group_by="total")
        sql = mock_execute.call_args[0][0]
        assert "InvoiceType" not in sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_get_kpi_summary_exclude_credits_true_adds_invoicetype_filter(self, mock_execute):
        from admcms_mcp.tools import get_kpi_summary

        mock_execute.return_value = [{}]
        get_kpi_summary("2026-01-01", "2026-02-01", include_ly=False, exclude_credits=True)
        header_sql = mock_execute.call_args_list[0][0][0]
        assert "InvoiceType = '0'" in header_sql

    @patch("admcms_mcp.tools.execute_query_params")
    def test_get_kpi_summary_exclude_credits_false_default_omits_filter(self, mock_execute):
        from admcms_mcp.tools import get_kpi_summary

        mock_execute.return_value = [{}]
        get_kpi_summary("2026-01-01", "2026-02-01", include_ly=False)
        header_sql = mock_execute.call_args_list[0][0][0]
        assert "InvoiceType" not in header_sql
