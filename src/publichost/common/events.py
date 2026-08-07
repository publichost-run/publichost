"""Async Redis event publisher for PublicHost."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis

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
            await self._client.publish(LOGS_CHANNEL, data)
        except redis.RedisError as exc:
            logger.warning("Failed to publish log event: %s", exc)
