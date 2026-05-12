from unittest.mock import patch

from admcms_mcp.tools import get_lookups, get_schema, query_database


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
    def test_uses_linetotal_and_invoicedate(self, mock_execute):
        from admcms_mcp.tools import get_revenue_by_business_unit

        mock_execute.return_value = []
        get_revenue_by_business_unit("2026-01-01", "2026-02-01")
        sql = mock_execute.call_args[0][0]
        params = mock_execute.call_args[0][1]
        assert "LineTotal" in sql
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
