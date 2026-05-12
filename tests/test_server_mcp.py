from httpx import ASGITransport, AsyncClient

from admcms_mcp.server import build_app


async def test_mcp_endpoint_exists():
    """A POST to /mcp with an empty body should not 404.

    We don't test the full MCP protocol here — MCP SDK has its own tests.
    We just verify the route is mounted.
    """
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            json={},
            headers={"Accept": "application/json, text/event-stream"},
        )
    # Expect 400/406/422 (bad request) rather than 404 (not found)
    assert response.status_code != 404
