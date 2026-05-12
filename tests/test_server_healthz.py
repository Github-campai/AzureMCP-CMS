from httpx import ASGITransport, AsyncClient

from admcms_mcp.server import build_app


async def test_healthz_returns_200_and_status():
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
