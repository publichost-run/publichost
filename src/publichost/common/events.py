"""Async Redis event publisher for PublicHost."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis

from .stats import (
    RELAY_HEARTBEAT_KEY,
    RELAY_HEARTBEAT_TTL_SECONDS,
    REQUEST_BUCKET_TTL_SECONDS,
    request_bucket_key,
)

logger = logging.getLogger(__name__)

TUNNEL_CHANNEL = "publichost:tunnels"
LOGS_CHANNEL = "publichost:logs"


class EventPublisher:
    """Publishes tunnel and request-log events to Redis."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        """Create the async Redis connection if a URL is configured."""
        if not self._redis_url or self._redis_url.lower() in ("none", ""):
            logger.info("Redis is disabled (no URL configured)")
            return
        self._client = await redis.from_url(self._redis_url, decode_responses=True)
        logger.info("Connected to Redis at %s", self._redis_url)

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def publish_tunnel(
        self,
        event: str,
        domain: str,
        token: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Publish a tunnel lifecycle event."""
        if self._client is None:
            return
        payload = {
            "event": event,
            "domain": domain,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if token is not None:
            payload["token"] = token
        payload.update(kwargs)
        try:
            data = json.dumps(payload)
            await self._client.publish(TUNNEL_CHANNEL, data)
        except redis.RedisError as exc:
            logger.warning("Failed to publish tunnel event: %s", exc)

    async def publish_log(
        self,
        domain: str,
        token: str,
        method: str,
        host: str,
        path: str,
        status_code: int | None,
        latency_ms: float,
        headers: dict[str, str],
        body: str,
    ) -> None:
        """Publish a WebLogs request entry."""
        if self._client is None:
            return
        payload = {
            "event": "log.request",
            "domain": domain,
            "token": token,
            "timestamp": datetime.now(UTC).isoformat(),
            "method": method,
            "host": host,
            "path": path,
            "status_code": status_code,
            "latency_ms": round(latency_ms, 3),
            "headers": headers,
            "body": body,
        }
        try:
            data = json.dumps(payload)
            # Ride a single pipelined round-trip: publish the log event and
            # bump the hourly request counter without adding awaits to the
            # proxy's hot path.
            bucket_key = request_bucket_key(datetime.now(UTC))
            pipe = self._client.pipeline(transaction=False)
            pipe.publish(LOGS_CHANNEL, data)
            pipe.incr(bucket_key)
            # nx=True: set the TTL only when the bucket key is first created, so
            # a hot bucket's expiry is not needlessly pushed out on every hit.
            pipe.expire(bucket_key, REQUEST_BUCKET_TTL_SECONDS, nx=True)
            await pipe.execute()
        except redis.RedisError as exc:
            logger.warning("Failed to publish log event: %s", exc)

    async def write_relay_heartbeat(self, active_tunnels: int, instance: str) -> None:
        """Write a periodic liveness/activity heartbeat for the public stats API."""
        if self._client is None:
            return
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "active_tunnels": active_tunnels,
            "instance": instance,
        }
        try:
            data = json.dumps(payload)
            await self._client.set(
                RELAY_HEARTBEAT_KEY, data, ex=RELAY_HEARTBEAT_TTL_SECONDS
            )
        except redis.RedisError as exc:
            logger.warning("Failed to write relay heartbeat: %s", exc)
