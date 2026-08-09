"""Public live-stats endpoint (`GET /api/v1/stats`).

Aggregates a few non-identifiable numbers for the marketing site:
relay health, active tunnel count, and 24h request volume. This endpoint is
public and unauthenticated, so it exposes ONLY the four ``StatsResponse``
fields — never subdomains, tokens, client IPs, or per-tunnel data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

import redis.asyncio as redis
from fastapi import APIRouter, Response
from sqlalchemy import func, select

from ...common.stats import (
    RELAY_HEARTBEAT_KEY,
    RELAY_OPERATIONAL_MAX_AGE_SECONDS,
    last_24h_bucket_keys,
)
from ..config import settings
from ..db import AsyncSessionLocal
from ..models import Tunnel
from ..schemas import StatsResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# One pooled Redis client reused across requests. This endpoint is polled
# frequently, so we avoid the per-connection open/close pattern used by the
# WebSocket handler.
_redis_client: redis.Redis | None = None
_redis_lock = asyncio.Lock()


async def _get_redis() -> redis.Redis:
    """Return the shared Redis client, creating it once under a lock."""
    global _redis_client
    if _redis_client is None:
        async with _redis_lock:
            if _redis_client is None:
                _redis_client = redis.from_url(
                    settings.redis_url, decode_responses=True
                )
    return _redis_client


async def close_redis() -> None:
    """Close the shared Redis client if it was created. Safe to always call."""
    global _redis_client
    if _redis_client is not None:
        client = _redis_client
        _redis_client = None
        await client.aclose()


async def _read_redis_stats() -> tuple[dict | None, int]:
    """Read the relay heartbeat and 24h request total from Redis.

    Returns ``(heartbeat_dict_or_None, requests_24h)``. A missing or
    unparseable heartbeat yields ``None``; missing request buckets count as 0.
    When Redis is disabled (e.g. the local-dev ``PUBLICHOST_REDIS_URL=none``
    default), skip the connection entirely and report no heartbeat / 0 requests.
    """
    if not settings.redis_url or settings.redis_url.lower() in ("none", ""):
        return None, 0
    client = await _get_redis()
    heartbeat_raw = await client.get(RELAY_HEARTBEAT_KEY)
    heartbeat: dict | None = None
    if heartbeat_raw is not None:
        try:
            parsed = json.loads(heartbeat_raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            heartbeat = parsed

    bucket_keys = last_24h_bucket_keys(datetime.now(UTC))
    values = await client.mget(bucket_keys)
    requests_24h = 0
    for value in values:
        if value is None:
            continue
        try:
            requests_24h += int(value)
        except (TypeError, ValueError):
            continue
    return heartbeat, requests_24h


async def _count_active_tunnels_db() -> int:
    """Count tunnels that are currently live (opened/extended, not expired)."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(func.count())
            .select_from(Tunnel)
            .where(
                Tunnel.status.in_(("opened", "extended")),
                Tunnel.expires_at > datetime.now(UTC),
            )
        )
        result = await session.execute(stmt)
        return int(result.scalar_one())


async def _safe_count_active_tunnels_db() -> int:
    """Count active tunnels, degrading to 0 if the DB is unreachable.

    This endpoint is designed to degrade gracefully: a fully offline local-dev
    environment (no Redis, no Postgres) must still yield a valid response rather
    than a 500. Any DB failure is logged and reported as 0 active tunnels.
    """
    try:
        return await _count_active_tunnels_db()
    except Exception:
        logger.warning("Failed to count active tunnels from DB", exc_info=True)
        return 0


def _heartbeat_age_seconds(heartbeat: dict) -> float | None:
    """Age of the heartbeat in seconds, or None if its ``ts`` is unusable."""
    ts_raw = heartbeat.get("ts")
    if not isinstance(ts_raw, str):
        return None
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


async def _build_stats() -> StatsResponse:
    """Assemble the live stats snapshot from Redis with a DB fallback."""
    relay_status: str
    active_tunnels: int
    requests_24h: int

    try:
        heartbeat, requests_24h = await _read_redis_stats()
    except Exception:
        # Redis connection failure (not a missing key): be honest, no data.
        # Log it so a genuine Redis outage in production is visible instead of
        # silently degrading forever (which would also mask real bugs).
        logger.warning("Failed to read stats from Redis", exc_info=True)
        relay_status = "degraded"
        active_tunnels = await _safe_count_active_tunnels_db()
        return _snapshot(active_tunnels, 0, relay_status)

    if heartbeat is None:
        relay_status = "down"
        active_tunnels = await _safe_count_active_tunnels_db()
        return _snapshot(active_tunnels, requests_24h, relay_status)

    # Heartbeat present: fresh -> operational, stale (up to the key TTL) or an
    # unusable timestamp -> degraded. (Absent key -> "down", handled above.)
    age = _heartbeat_age_seconds(heartbeat)
    if age is not None and age <= RELAY_OPERATIONAL_MAX_AGE_SECONDS:
        relay_status = "operational"
    else:
        relay_status = "degraded"

    raw_active = heartbeat.get("active_tunnels")
    if isinstance(raw_active, int):
        active_tunnels = raw_active
    else:
        active_tunnels = await _safe_count_active_tunnels_db()

    return _snapshot(active_tunnels, requests_24h, relay_status)


def _snapshot(
    active_tunnels: int, requests_24h: int, relay_status: str
) -> StatsResponse:
    """Build a StatsResponse with a freshly stamped UTC ``updated_at``."""
    return StatsResponse(
        active_tunnels=active_tunnels,
        requests_24h=requests_24h,
        relay_status=relay_status,
        updated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


# In-process memoization to shield Redis/DB from the marketing site's polling.
_cache_lock = asyncio.Lock()
_cache: tuple[float, StatsResponse] | None = None


async def _cached_stats() -> StatsResponse:
    """Return a cached snapshot, rebuilding at most once per TTL window."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now < _cache[0]:
        return _cache[1]
    async with _cache_lock:
        now = time.monotonic()
        if _cache is not None and now < _cache[0]:
            return _cache[1]
        snapshot = await _build_stats()
        _cache = (now + settings.stats_cache_seconds, snapshot)
        return snapshot


@router.get("/api/v1/stats", response_model=StatsResponse)
async def stats(response: Response) -> StatsResponse:
    """Return public live numbers for the marketing site."""
    response.headers["Cache-Control"] = (
        f"public, max-age={settings.stats_cache_seconds}"
    )
    return await _cached_stats()
