"""Celery application for background tasks."""

from __future__ import annotations

from celery import Celery

from ..config import settings

celery_app = Celery(
    "publichost",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["publichost.api.celery.tasks"],
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "cleanup-expired-tunnels": {
            "task": "publichost.cleanup_expired_tunnels",
            "schedule": 60.0,
        },
    },
)
