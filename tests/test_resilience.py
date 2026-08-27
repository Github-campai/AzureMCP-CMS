"""The server keeps answering while a query runs, and one user cannot take it over.

The first class is the regression test for the disconnects seen in production:
the MCP SDK calls a synchronous tool function straight on the event loop, so a
long query froze the whole process. `/healthz` stopped answering, Azure's
liveness probe concluded the container was dead and restarted it, and every
client connected to that replica dropped. `ContainerAppSystemLogs` recorded 13
of those on 2026-08-27 alone.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from admcms_mcp import limits
from admcms_mcp.limits import ConcurrencyLimitReached, UserConcurrencyLimiter
from admcms_mcp.server import _audited, build_app


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("KEY_VAULT_URI", "https://kv.vault.azure.net/")
    monkeypatch.setenv("ENTRA_APP_ID", "api://admcms-mcp")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANTS", "campai-tenant-id")
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("AUDIT_STORE", "none")
    monkeypatch.delenv("MAX_CONCURRENT_QUERIES_PER_USER", raising=False)
    monkeypatch.delenv("QUERY_QUEUE_TIMEOUT_SECONDS", raising=False)
    from admcms_mcp.audit import set_store
    from admcms_mcp.audit_stores import NullStore
    from admcms_mcp.config import get_settings

    get_settings.cache_clear()
    set_store(NullStore())
    yield
    get_settings.cache_clear()


def _slow_tool(seconds: float = 0.4):
    def call():
        time.sleep(seconds)  # stands in for a long-running pyodbc query
        return {"data": [{"ok": True}], "summary": None, "error": None}

    return call


class TestEventLoopStaysFree:
    async def test_a_slow_tool_does_not_freeze_the_loop(self):
        ticks = 0
        stop = False

        async def heartbeat():
            nonlocal ticks
            while not stop:
                await asyncio.sleep(0.02)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            result = await _audited("get_revenue", {}, _slow_tool())
        finally:
            stop = True
            await beat

        assert result["data"] == [{"ok": True}]
        assert ticks >= 5, f"the loop was blocked during the call (only {ticks} ticks)"

    async def test_healthz_answers_while_a_tool_is_running(self):
        """This is the exact thing Azure's liveness probe checks."""
        app = build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            running = asyncio.create_task(_audited("get_revenue", {}, _slow_tool(0.5)))
            await asyncio.sleep(0.05)  # let it get going

            start = time.monotonic()
            response = await client.get("/healthz")
            elapsed = time.monotonic() - start

            await running

        assert response.status_code == 200
        assert elapsed < 0.2, f"/healthz waited {elapsed:.2f}s on the running query"

    async def test_two_tool_calls_overlap(self):
        start = time.monotonic()
        results = await asyncio.gather(
            _audited("get_revenue", {}, _slow_tool(0.4)),
            _audited("get_kpi_summary", {}, _slow_tool(0.4)),
        )
        elapsed = time.monotonic() - start

        assert all(r["data"] for r in results)
        assert elapsed < 0.7, f"the calls were serialised ({elapsed:.2f}s for 2x0.4s)"


class TestPerUserLimit:
    async def test_calls_beyond_the_limit_wait_rather_than_fail(self, monkeypatch):
        monkeypatch.setenv("MAX_CONCURRENT_QUERIES_PER_USER", "2")
        limiter = UserConcurrencyLimiter()
        order: list[str] = []

        async def work(name: str):
            async with limiter.slot("user-a", tool_name=name):
                order.append(f"start-{name}")
                await asyncio.sleep(0.05)
                order.append(f"done-{name}")

        await asyncio.gather(work("1"), work("2"), work("3"))

        assert len(order) == 6
        # The third could not start before one of the first two finished.
        assert order.index("start-3") > order.index("done-1")

    async def test_the_limit_is_per_user(self, monkeypatch):
        monkeypatch.setenv("MAX_CONCURRENT_QUERIES_PER_USER", "1")
        limiter = UserConcurrencyLimiter()
        concurrent = 0
        peak = 0

        async def work(subject: str):
            nonlocal concurrent, peak
            async with limiter.slot(subject):
                concurrent += 1
                peak = max(peak, concurrent)
                await asyncio.sleep(0.05)
                concurrent -= 1

        await asyncio.gather(work("user-a"), work("user-b"))

        assert peak == 2, "two different users must not queue behind each other"

    async def test_a_caller_that_never_gets_a_slot_is_refused(self, monkeypatch):
        monkeypatch.setenv("MAX_CONCURRENT_QUERIES_PER_USER", "1")
        monkeypatch.setenv("QUERY_QUEUE_TIMEOUT_SECONDS", "0.05")
        limiter = UserConcurrencyLimiter()

        async with limiter.slot("user-a"):
            with pytest.raises(ConcurrencyLimitReached) as excinfo:
                async with limiter.slot("user-a"):
                    pass

        message = str(excinfo.value)
        assert "1 queries running" in message
        assert "narrow the date range" in message, "the refusal must say what to do next"

    async def test_zero_disables_the_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_CONCURRENT_QUERIES_PER_USER", "0")
        limiter = UserConcurrencyLimiter()
        concurrent = 0
        peak = 0

        async def work():
            nonlocal concurrent, peak
            async with limiter.slot("user-a"):
                concurrent += 1
                peak = max(peak, concurrent)
                await asyncio.sleep(0.02)
                concurrent -= 1

        await asyncio.gather(*(work() for _ in range(5)))

        assert peak == 5

    async def test_idle_callers_are_forgotten(self, monkeypatch):
        """The dict must track live users, not everyone who ever connected."""
        monkeypatch.setenv("MAX_CONCURRENT_QUERIES_PER_USER", "2")
        limiter = UserConcurrencyLimiter()

        async with limiter.slot("user-a"):
            assert limiter.in_flight("user-a") == 1

        assert limiter.in_flight("user-a") == 0
        assert limiter._semaphores == {}

    async def test_a_refused_call_comes_back_as_a_normal_error_response(self, monkeypatch):
        """The model gets a readable answer, not an exception."""
        monkeypatch.setenv("MAX_CONCURRENT_QUERIES_PER_USER", "1")
        monkeypatch.setenv("QUERY_QUEUE_TIMEOUT_SECONDS", "0.05")
        monkeypatch.setattr(limits, "limiter", UserConcurrencyLimiter())
        from admcms_mcp import server

        monkeypatch.setattr(server, "limiter", limits.limiter)

        slow = asyncio.create_task(_audited("get_revenue", {}, _slow_tool(0.4)))
        await asyncio.sleep(0.05)
        refused = await _audited("get_kpi_summary", {}, _slow_tool(0.1))
        await slow

        assert refused["data"] == []
        assert "queries running" in refused["error"]


class TestAuthDoesNotBlockTheLoop:
    """JWKS goes out over sync httpx and the allowlist through the Key Vault SDK.

    Both miss regularly — the allowlist every five minutes by design — and on
    the event loop that stalls every request in flight, not just the one being
    authenticated.
    """

    async def test_a_slow_token_check_does_not_stall_other_requests(self, monkeypatch):
        monkeypatch.setenv("AUTH_DISABLED", "false")
        from admcms_mcp.config import get_settings

        get_settings.cache_clear()

        def slow_validate(token):
            time.sleep(0.4)  # stands in for the JWKS fetch on a cold cache
            return {"oid": "u", "upn": "u@campai.nl", "tid": "campai-tenant-id"}

        from admcms_mcp import server

        monkeypatch.setattr(server, "validate_jwt", slow_validate)
        monkeypatch.setattr(server, "check_access", lambda claims: None)

        app = build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            authenticating = asyncio.create_task(
                client.get("/admin/health", headers={"Authorization": "Bearer x"})
            )
            await asyncio.sleep(0.05)

            start = time.monotonic()
            health = await client.get("/healthz")
            elapsed = time.monotonic() - start

            await authenticating

        assert health.status_code == 200
        assert elapsed < 0.2, f"/healthz waited {elapsed:.2f}s on a token check"


class TestSingleFlightCaches:
    def test_concurrent_misses_fetch_the_allowlist_once(self, monkeypatch):
        """Without the lock, every request on an expired cache calls Key Vault."""
        import threading

        from admcms_mcp import auth

        auth._ALLOWLIST_CACHE["data"] = None
        auth._ALLOWLIST_CACHE["fetched_at"] = 0.0
        calls = 0

        def slow_secret(name):
            nonlocal calls
            calls += 1
            time.sleep(0.1)
            return '{"users": [], "groups": []}'

        monkeypatch.setattr(auth, "get_secret", slow_secret)

        threads = [threading.Thread(target=auth._load_allowlist) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert calls == 1, f"the allowlist was fetched {calls} times for one miss"

    def test_concurrent_misses_fetch_the_jwks_once(self, monkeypatch):
        import threading

        from admcms_mcp import auth

        auth._JWKS_CACHE["keys"] = None
        auth._JWKS_CACHE["fetched_at"] = 0.0
        calls = 0

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"keys": [{"kid": "k1"}]}

        def slow_get(url, timeout=10):
            nonlocal calls
            calls += 1
            time.sleep(0.1)
            return FakeResponse()

        monkeypatch.setattr(auth.httpx, "get", slow_get)

        threads = [threading.Thread(target=auth._fetch_jwks_raw) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert calls == 1, f"the JWKS was fetched {calls} times for one miss"
