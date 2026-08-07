"""Redis pub/sub consumer for the FastAPI backend."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
from sqlalchemy import select

from .config import settings
from .db import AsyncSessionLocal
from .models import Tunnel

logger = logging.getLogger(__name__)


async def _handle_tunnel_event(payload: dict[str, Any]) -> None:
    """Persist tunnel lifecycle events to PostgreSQL."""
    event = payload.get("event")
    domain = payload.get("domain")
    token = payload.get("token")
    if not domain or not token:
        return

    try:
        async with AsyncSessionLocal() as session:
            # Try to find an existing record by token.
            result = await session.execute(select(Tunnel).where(Tunnel.token == token))
            existing = result.scalar_one_or_none()

            if event == "tunnel.opened":
                if existing is not None:
                    # Duplicate or reconnect; update status.
                    existing.status = "opened"
                    existing.expires_at = _parse_iso(payload.get("expires_at"))
                else:
                    tunnel = Tunnel(
                        subdomain=domain.split(".")[0],
                        token=token,
                        domain=domain,
                        ssh_domain=payload.get("ssh_domain", ""),
                        frontend_domain=payload.get("frontend_domain", ""),
                        client_ip=payload.get("client_ip"),
                        status="opened",
                        created_at=_parse_iso(payload.get("created_at")),
                        expires_at=_parse_iso(payload.get("expires_at")),
                        listener_port=payload.get("listener_port"),
                    )
                    session.add(tunnel)

            elif existing is not None:
                if event == "tunnel.closed":
                    existing.status = "closed"
                    existing.released_at = datetime.now(UTC)
                elif event == "tunnel.expired":
                    existing.status = "expired"
                    existing.released_at = datetime.now(UTC)
                elif event == "tunnel.extended":
                    existing.status = "extended"
                    existing.expires_at = _parse_iso(payload.get("expires_at"))

            await session.commit()
    except Exception:
        logger.exception("Failed to handle tunnel event %s", event)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def consume_redis() -> None:
    """Subscribe to Redis channels and process events."""
    if not settings.redis_url or settings.redis_url.lower() in ("none", ""):
        logger.info("Redis consumer disabled (no URL configured)")
        return

    client = await redis.from_url(settings.redis_url, decode_responses=True)
    try:
        pubsub = client.pubsub()
        await pubsub.subscribe("publichost:tunnels")
        logger.info("Subscribed to Redis tunnels channel")
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                payload = json.loads(message["data"])
            except json.JSONDecodeError:
                logger.warning("Received invalid JSON from Redis: %r", message["data"])
                continue
            channel = message["channel"]
            if channel == "publichost:tunnels":
                await _handle_tunnel_event(payload)
    finally:
        await client.close()
