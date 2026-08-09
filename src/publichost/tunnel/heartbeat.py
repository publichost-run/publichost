"""Background task that publishes a periodic relay liveness heartbeat."""

from __future__ import annotations

import asyncio
import logging

from ..common.events import EventPublisher
from .registry import Registry

logger = logging.getLogger(__name__)


async def run_heartbeat(
    registry: Registry,
    publisher: EventPublisher,
    interval_seconds: int,
    instance: str,
) -> None:
    """Publish a relay heartbeat on an interval.

    Lets the public stats API report relay liveness and tunnel activity.
    """
    while True:
        try:
            await publisher.write_relay_heartbeat(
                registry.active_tunnel_count, instance
            )
        except Exception:
            logger.exception("Failed to publish relay heartbeat")
        await asyncio.sleep(interval_seconds)
