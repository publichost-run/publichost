"""ASGI-level smoke tests for the public REST routes.

Uses httpx's ASGITransport, which bypasses the lifespan context (no live DB or
Redis needed) since the data layer is monkeypatched directly.
"""

from __future__ import annotations

import httpx
import pytest

from publichost.api.main import app
from publichost.api.routers import stats
from publichost.api.schemas import CliVersionResponse, StatsResponse


@pytest.fixture
def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_cli_version_route(_client) -> None:
    async with _client as client:
        resp = await client.get("/api/v1/cli/version")

    assert resp.status_code == 200
    assert set(resp.json().keys()) == set(CliVersionResponse.model_fields.keys())
    assert resp.headers["Cache-Control"] == "public, max-age=300"


async def test_stats_route(monkeypatch, _client) -> None:
    fixed = StatsResponse(
        active_tunnels=5,
        requests_24h=100,
        relay_status="operational",
        updated_at="2026-08-09T12:00:00Z",
    )

    async def fake_build() -> StatsResponse:
        return fixed

    stats._cache = None
    monkeypatch.setattr(stats, "_build_stats", fake_build)

    async with _client as client:
        resp = await client.get("/api/v1/stats")

    assert resp.status_code == 200
    assert set(resp.json().keys()) == set(StatsResponse.model_fields.keys())
    assert resp.json() == fixed.model_dump()
    assert resp.headers["Cache-Control"].startswith("public, max-age=")

    stats._cache = None
