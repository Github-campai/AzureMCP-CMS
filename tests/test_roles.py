"""Rollen per tool: wie ziet wat, en wie mag wat aanroepen."""

from __future__ import annotations

import pytest

from admcms_mcp import roles
from admcms_mcp.server import _audited, _build_mcp_server, _dev_roles


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    monkeypatch.setenv("ROLE_ENFORCEMENT", "enforce")
    monkeypatch.setenv("ROLE_DEFAULT", "viewer")
    monkeypatch.setenv("AUDIT_STORE", "none")
    from admcms_mcp.audit import set_store
    from admcms_mcp.audit_stores import NullStore
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()
    set_store(NullStore())
    yield
    get_settings.cache_clear()


@pytest.fixture
def as_roles():
    """Set the caller's roles for one test.

    Reset by assignment, not by token: an async test body runs in its own
    context, so a token taken during setup cannot be reset afterwards.
    """

    def _set(*names: str):
        roles.current_roles.set(frozenset(names))

    yield _set
    roles.current_roles.set(frozenset())


class TestRolesFromClaims:
    def test_an_entra_app_role_becomes_a_role(self):
        assert roles.roles_from_claims({"roles": ["admcms.finance"]}) == frozenset({"finance"})

    def test_case_and_separator_do_not_matter(self):
        claims = {"roles": ["Finance", "ADMCMS.IT", "admcms-sales"]}
        assert roles.roles_from_claims(claims) == frozenset({"finance", "it", "sales"})

    def test_a_space_separated_string_also_works(self):
        assert roles.roles_from_claims({"roles": "admcms.it admcms.sales"}) == frozenset(
            {"it", "sales"}
        )

    def test_the_service_principal_role_is_not_a_role(self):
        """`admcms.invoke` shares the claim but means something else."""
        assert roles.roles_from_claims({"roles": ["admcms.invoke"]}) == frozenset({"viewer"})

    def test_no_app_role_falls_back_to_the_default(self):
        assert roles.roles_from_claims({"upn": "x@cmsnl.com"}) == frozenset({"viewer"})

    def test_the_default_is_configurable(self, monkeypatch):
        monkeypatch.setenv("ROLE_DEFAULT", "sales")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        assert roles.roles_from_claims({}) == frozenset({"sales"})

    def test_the_claim_name_is_configurable(self, monkeypatch):
        monkeypatch.setenv("ROLE_CLAIM", "extension_roles")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        assert roles.roles_from_claims({"extension_roles": ["admcms.it"]}) == frozenset({"it"})


class TestWhatEachRoleMay:
    def test_finance_gets_the_money_tools(self, as_roles):
        as_roles("finance")
        assert roles.can_call("get_margin_report")
        assert roles.can_call("get_kpi_summary")
        assert roles.can_call("get_discount_analysis")

    def test_finance_may_not_run_free_form_sql(self, as_roles):
        as_roles("finance")
        assert not roles.can_call("query_database")

    def test_it_gets_the_technical_tools_but_not_the_numbers(self, as_roles):
        as_roles("it")
        assert roles.can_call("query_database")
        assert roles.can_call("detect_price_anomalies")
        assert not roles.can_call("get_margin_report")
        assert not roles.can_call("get_revenue")

    def test_every_role_can_orient_itself(self, as_roles):
        """describe_server and health_check must never be role-gated away."""
        for role in ("finance", "it", "sales", "viewer"):
            as_roles(role)
            assert roles.can_call("describe_server"), role
            assert roles.can_call("health_check"), role
            assert roles.can_call("get_schema"), role

    def test_admin_may_call_everything(self, as_roles):
        as_roles("admin")
        assert roles.allowed_tools(frozenset({"admin"})) is None
        assert roles.can_call("query_database")
        assert roles.can_call("get_margin_report")

    def test_roles_add_up(self, as_roles):
        as_roles("finance", "it")
        assert roles.can_call("get_margin_report")
        assert roles.can_call("query_database")

    def test_without_a_role_nothing_is_allowed(self, as_roles):
        as_roles()
        assert not roles.can_call("get_revenue")
        assert not roles.can_call("describe_server")


class TestEnforcementModes:
    def test_enforce_refuses(self, as_roles):
        as_roles("finance")
        with pytest.raises(roles.RoleDenied):
            roles.require_tool("query_database")

    def test_warn_only_logs(self, as_roles, monkeypatch, caplog):
        monkeypatch.setenv("ROLE_ENFORCEMENT", "warn")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        as_roles("finance")

        with caplog.at_level("WARNING"):
            roles.require_tool("query_database")

        assert "would be refused" in caplog.text

    def test_off_skips_the_check(self, as_roles, monkeypatch, caplog):
        monkeypatch.setenv("ROLE_ENFORCEMENT", "off")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        as_roles()

        with caplog.at_level("WARNING"):
            roles.require_tool("query_database")

        assert caplog.text == ""

    def test_an_unknown_mode_falls_back_to_warn(self, monkeypatch):
        monkeypatch.setenv("ROLE_ENFORCEMENT", "onzin")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        assert roles.enforcement() == "warn"


class TestToolsListFiltering:
    async def test_finance_does_not_see_query_database(self, as_roles):
        as_roles("finance")
        names = {t.name for t in await _build_mcp_server().list_tools()}
        assert "get_margin_report" in names
        assert "query_database" not in names

    async def test_it_sees_query_database_but_no_margins(self, as_roles):
        as_roles("it")
        names = {t.name for t in await _build_mcp_server().list_tools()}
        assert "query_database" in names
        assert "get_margin_report" not in names

    async def test_admin_sees_everything(self, as_roles):
        as_roles("admin")
        mcp = _build_mcp_server()
        assert len(await mcp.list_tools()) == len(mcp._tool_manager.list_tools())

    async def test_warn_mode_hides_nothing(self, as_roles, monkeypatch):
        monkeypatch.setenv("ROLE_ENFORCEMENT", "warn")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        as_roles("finance")
        names = {t.name for t in await _build_mcp_server().list_tools()}
        assert "query_database" in names

    async def test_describe_server_follows_the_filter(self, as_roles):
        """It reads the same list, so it cannot advertise what you may not call."""
        as_roles("finance")
        mcp = _build_mcp_server()
        described = await mcp._tool_manager.get_tool("describe_server").fn()

        names = {tool["naam"] for tool in described["tools"]}
        assert "query_database" not in names
        assert described["jij"]["rollen"] == ["finance"]


class TestCallEnforcement:
    async def test_a_refused_call_never_reaches_the_tool(self, as_roles):
        as_roles("finance")
        called = False

        def tool():
            nonlocal called
            called = True
            return {"data": [], "summary": None, "error": None}

        result = await _audited("query_database", {"sql": "SELECT 1"}, tool)

        assert called is False
        assert "hoort niet bij je rol" in result["error"]
        assert result["data"] == []

    async def test_an_allowed_call_runs(self, as_roles):
        as_roles("finance")
        result = await _audited(
            "get_revenue",
            {"start_date": "2026-01-01"},
            lambda: {"data": [{"amount": 1}], "summary": None, "error": None},
        )
        assert result["data"] == [{"amount": 1}]

    async def test_warn_mode_still_runs_the_tool(self, as_roles, monkeypatch):
        monkeypatch.setenv("ROLE_ENFORCEMENT", "warn")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        as_roles("finance")

        result = await _audited(
            "query_database",
            {"sql": "SELECT 1"},
            lambda: {"data": [{"x": 1}], "summary": None, "error": None},
        )
        assert result["data"] == [{"x": 1}]

    async def test_the_refusal_is_written_to_the_audit_log(self, as_roles):
        from admcms_mcp import audit

        logged = []

        class CapturingStore:
            def log(self, entry):
                logged.append(entry)

        audit.set_store(CapturingStore())
        try:
            as_roles("finance")
            await _audited("query_database", {"sql": "SELECT 1"}, lambda: {"data": []})
        finally:
            audit.set_store(None)

        assert len(logged) == 1
        assert logged[0].tool_name == "query_database"
        assert "hoort niet bij je rol" in logged[0].error


class TestDevRoles:
    def test_dev_roles_is_parsed(self, monkeypatch):
        monkeypatch.setenv("DEV_ROLES", "finance, it")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        assert _dev_roles(get_settings()) == frozenset({"finance", "it"})

    def test_nonsense_falls_back_to_admin(self, monkeypatch):
        monkeypatch.setenv("DEV_ROLES", "banaan")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()
        assert _dev_roles(get_settings()) == frozenset({"admin"})


class TestTheTableStaysHonest:
    """Guards that catch a rename or a new tool before production does."""

    def test_every_tool_in_the_table_exists(self):
        registered = {tool.name for tool in _build_mcp_server()._tool_manager.list_tools()}
        referenced = set().union(*(t for t in roles.ROLE_TOOLS.values())) - {"*"}
        assert referenced <= registered, f"unknown tools in ROLE_TOOLS: {referenced - registered}"

    def test_every_tool_is_reachable_by_some_role(self):
        """A tool nobody can call is a configuration bug, not a feature."""
        registered = {tool.name for tool in _build_mcp_server()._tool_manager.list_tools()}
        granted = set().union(*(t for t in roles.ROLE_TOOLS.values())) - {"*"}
        assert registered <= granted, f"tools no role grants: {registered - granted}"

    def test_describe_lists_every_role(self):
        described = roles.describe()
        assert set(described) == set(roles.ROLE_TOOLS)
        assert described["admin"] == ["*"]
