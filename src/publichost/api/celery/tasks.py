"""Celery tasks for the PublicHost backend."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from ..db import AsyncSessionLocal, engine
from ..models import Tunnel
from .app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="publichost.cleanup_expired_tunnels")
def cleanup_expired_tunnels() -> int:
    """Mark active tunnels as expired when their lifetime has passed.

    Celery cannot execute coroutine functions directly, so this task is a
    plain synchronous entry point that drives the async cleanup body via
    ``asyncio.run``.
    """
    return asyncio.run(_cleanup_expired_tunnels_async())


async def _cleanup_expired_tunnels_async() -> int:
    """Async implementation of the expired-tunnel cleanup.

    PublicHost itself closes the SSH connection; this task only syncs DB state.

    Each call runs inside a fresh event loop (``asyncio.run`` in the sync task
    wrapper), but the async engine's connection pool caches asyncpg
    connections bound to the loop that created them. Without disposing the
    engine before this loop closes, the next invocation reuses connections
    tied to an already-closed loop and fails. Since this only runs a few
    times a minute, disposing (and letting the pool reconnect next call) is
    cheap and keeps the task correct across repeated invocations.
    """
    now = datetime.now(UTC)
    count = 0
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Tunnel).where(
                    Tunnel.status.in_(["opened", "extended"]),
                    Tunnel.expires_at <= now,
                )
            )
            for tunnel in result.scalars():
                tunnel.status = "expired"
                tunnel.released_at = now
                count += 1
            await session.commit()
    finally:
        await engine.dispose()
    if count:
        logger.info("Marked %d tunnels as expired", count)
    return count
