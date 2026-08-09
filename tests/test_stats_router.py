"""Tests for the public /api/v1/stats endpoint.

All Redis and DB access is faked so no live Postgres/Redis is required.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from publichost.api.routers import stats
from publichost.common.stats import (
    RELAY_HEARTBEAT_TTL_SECONDS,
    RELAY_OPERATIONAL_MAX_AGE_SECONDS,
)


class FakeRedis:
    """Minimal async Redis stand-in with call counters."""

    def __init__(
        self, heartbeat_raw: str | None = None, mget_values: list | None = None
    ) -> None:
        self._heartbeat_raw = heartbeat_raw
        self._mget_values = mget_values or []
        self.get_calls = 0
        self.mget_calls = 0
        self.closed = False

    async def get(self, key: str):
        self.get_calls += 1
        return self._heartbeat_raw

    async def mget(self, keys):
        self.mget_calls += 1
        # Ignore the concrete keys; return a caller-provided value vector.
        return list(self._mget_values)

    async def aclose(self) -> None:
        self.closed = True


def _heartbeat(age_seconds: float, active_tunnels: int = 42) -> str:
    ts = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return json.dumps({"ts": ts.isoformat(), "active_tunnels": active_tunnels})


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with an empty memoization cache."""
    stats._cache = None
    yield
    stats._cache = None


def _install_redis(monkeypatch, fake: FakeRedis) -> None:
    async def fake_get_redis() -> FakeRedis:
        return fake

    monkeypatch.setattr(stats, "_get_redis", fake_get_redis)


def _install_db_count(monkeypatch, value: int) -> list[int]:
    calls: list[int] = []

    async def fake_count() -> int:
        calls.append(1)
        return value

    monkeypatch.setattr(stats, "_count_active_tunnels_db", fake_count)
    return calls


# --------------------------------------------------------------------------- #
# relay_status thresholds
# --------------------------------------------------------------------------- #


async def test_operational_at_boundary(monkeypatch) -> None:
    fake = FakeRedis(_heartbeat(RELAY_OPERATIONAL_MAX_AGE_SECONDS - 1), mget_values=[])
    _install_redis(monkeypatch, fake)
    _install_db_count(monkeypatch, 0)

    result = await stats._build_stats()

    assert result.relay_status == "operational"
    assert result.active_tunnels == 42  # from heartbeat, not DB


async def test_degraded_just_past_operational_boundary(monkeypatch) -> None:
    fake = FakeRedis(_heartbeat(RELAY_OPERATIONAL_MAX_AGE_SECONDS + 5), mget_values=[])
    _install_redis(monkeypatch, fake)
    _install_db_count(monkeypatch, 0)

    result = await stats._build_stats()

    assert result.relay_status == "degraded"


async def test_degraded_when_stale_within_ttl(monkeypatch) -> None:
    fake = FakeRedis(_heartbeat(RELAY_HEARTBEAT_TTL_SECONDS - 1), mget_values=[])
    _install_redis(monkeypatch, fake)
    _install_db_count(monkeypatch, 0)

    result = await stats._build_stats()

    assert result.relay_status == "degraded"


async def test_down_when_heartbeat_missing_uses_db_fallback(monkeypatch) -> None:
    fake = FakeRedis(heartbeat_raw=None, mget_values=["3", "4"])
    _install_redis(monkeypatch, fake)
    db_calls = _install_db_count(monkeypatch, 17)

    result = await stats._build_stats()

    assert result.relay_status == "down"
    assert result.active_tunnels == 17  # DB fallback
    assert len(db_calls) == 1
    assert result.requests_24h == 7


async def test_redis_error_degraded_with_db_fallback_and_zero_requests(
    monkeypatch,
) -> None:
    async def boom() -> FakeRedis:
        raise ConnectionError("redis down")

    monkeypatch.setattr(stats, "_get_redis", lambda: boom())
    db_calls = _install_db_count(monkeypatch, 9)

    result = await stats._build_stats()

    assert result.relay_status == "degraded"
    assert result.active_tunnels == 9
    assert result.requests_24h == 0
    assert len(db_calls) == 1


# --------------------------------------------------------------------------- #
# graceful degradation with Redis disabled / DB unavailable
# --------------------------------------------------------------------------- #


async def test_redis_disabled_reports_no_heartbeat(monkeypatch) -> None:
    # Local-dev default: PUBLICHOST_REDIS_URL=none. Must not attempt a
    # connection nor raise; relay_status reflects the absent heartbeat.
    monkeypatch.setattr(stats.settings, "redis_url", "none")
    _install_db_count(monkeypatch, 4)

    def _fail_get_redis() -> None:  # pragma: no cover - must never be awaited
        raise AssertionError("Redis client must not be created when disabled")

    monkeypatch.setattr(stats, "_get_redis", _fail_get_redis)

    result = await stats._build_stats()

    assert result.relay_status != "operational"
    assert result.relay_status == "down"
    assert result.active_tunnels == 4
    assert result.requests_24h == 0


async def test_redis_disabled_and_db_failure_returns_down_not_500(monkeypatch) -> None:
    # Fully offline local dev: no Redis and no Postgres. The endpoint must still
    # return a valid StatsResponse (active_tunnels=0, relay_status="down"),
    # never propagate an exception into an HTTP 500.
    monkeypatch.setattr(stats.settings, "redis_url", "none")

    async def boom_db() -> int:
        raise ConnectionError("no postgres")

    monkeypatch.setattr(stats, "_count_active_tunnels_db", boom_db)

    result = await stats._build_stats()

    assert result.relay_status == "down"
    assert result.active_tunnels == 0
    assert result.requests_24h == 0


# --------------------------------------------------------------------------- #
# request bucket summation
# --------------------------------------------------------------------------- #


async def test_mget_sums_treating_none_as_zero(monkeypatch) -> None:
    fake = FakeRedis(mget_values=["10", None, "5", None, "100"])
    _install_redis(monkeypatch, fake)

    heartbeat, requests_24h = await stats._read_redis_stats()

    assert heartbeat is None
    assert requests_24h == 115


async def test_updated_at_is_iso_z_format(monkeypatch) -> None:
    fake = FakeRedis(_heartbeat(1), mget_values=[])
    _install_redis(monkeypatch, fake)
    _install_db_count(monkeypatch, 0)

    result = await stats._build_stats()

    # Parses as ISO-8601 with a trailing Z.
    assert result.updated_at.endswith("Z")
    datetime.strptime(result.updated_at, "%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# in-process memoization
# --------------------------------------------------------------------------- #


async def test_cache_hits_data_layer_once_within_ttl(monkeypatch) -> None:
    monkeypatch.setattr(stats.settings, "stats_cache_seconds", 60)
    fake = FakeRedis(_heartbeat(1), mget_values=["1"])
    _install_redis(monkeypatch, fake)
    _install_db_count(monkeypatch, 0)

    first = await stats._cached_stats()
    second = await stats._cached_stats()

    assert first is second
    assert fake.get_calls == 1
    assert fake.mget_calls == 1


async def test_cache_rebuilds_after_ttl_expires(monkeypatch) -> None:
    monkeypatch.setattr(stats.settings, "stats_cache_seconds", 0.05)
    fake = FakeRedis(_heartbeat(1), mget_values=["1"])
    _install_redis(monkeypatch, fake)
    _install_db_count(monkeypatch, 0)

    await stats._cached_stats()
    await stats._cached_stats()
    assert fake.get_calls == 1

    await asyncio.sleep(0.1)
    await stats._cached_stats()
    assert fake.get_calls == 2
