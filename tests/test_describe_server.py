"""describe_server must stay in step with the real tool registry."""

import pytest


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "tenant-a")
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _call_describe():
    """Invoke the tool through the MCP registry, as a client would.

    call_tool returns a list of content blocks; the dict comes back as JSON text.
    """
    import json

    from admcms_mcp.server import _build_mcp_server

    mcp = _build_mcp_server()
    blocks = await mcp.call_tool("describe_server", {})
    if isinstance(blocks, tuple):
        blocks = blocks[0]
    return json.loads(blocks[0].text)


async def test_describe_server_is_registered():
    from admcms_mcp.server import _build_mcp_server

    names = {t.name for t in await _build_mcp_server().list_tools()}
    assert "describe_server" in names


async def test_lists_every_registered_tool():
    """Hardcoding an inventory would drift; this asserts it is derived."""
    from admcms_mcp.server import _build_mcp_server

    mcp = _build_mcp_server()
    registry = {t.name for t in await mcp.list_tools()}
    out = await _call_describe()

    described = {t["naam"] for t in out["tools"]}
    assert described == registry
    assert out["server"]["aantal_tools"] == len(registry)


async def test_flags_filters_and_paging_from_the_schema():
    out = await _call_describe()
    by_name = {t["naam"]: t for t in out["tools"]}

    assert by_name["get_top_customers"]["ondersteunt_filters"] is True
    assert by_name["health_check"]["ondersteunt_filters"] is False
    assert by_name["search_products"]["ondersteunt_paginatie"] is True
    assert by_name["health_check"]["ondersteunt_paginatie"] is False


async def test_states_the_row_limit_and_how_truncation_shows_up():
    from admcms_mcp.db import _MAX_ROWS_HARD_LIMIT

    out = await _call_describe()
    assert out["limieten"]["max_rijen_per_query"] == _MAX_ROWS_HARD_LIMIT
    assert "truncated" in out["limieten"]["afkapping"]
    assert "max_per_dim" in out["limieten"]["filterwaarden"]


async def test_names_the_caller():
    from admcms_mcp.audit import current_user

    current_user.set("iemand@campai.nl")
    out = await _call_describe()
    assert out["jij"]["identiteit"] == "iemand@campai.nl"


async def test_warns_about_the_data_traps_that_cause_wrong_totals():
    out = await _call_describe()
    letop = " ".join(out["let_op"]).lower()
    # The empty DeliveryCountry is why summing per country misses revenue.
    assert "deliverycountry" in letop
    # IC means two different things.
    assert "intercompany" in letop
    # business_unit does not cover every customer group.
    assert "customer_group" in letop
