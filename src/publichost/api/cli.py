"""CLI entry points for the FastAPI backend and Celery."""

from __future__ import annotations

import uvicorn

from .config import settings


def run_api() -> None:
    """Run the FastAPI server with uvicorn."""
    uvicorn.run(
        "publichost.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


def run_worker() -> None:
    """Run a Celery worker."""
    from .celery.app import celery_app

    celery_app.worker_main(argv=["worker", "-l", "info"])


def run_beat() -> None:
    """Run the Celery beat scheduler."""
    from .celery.app import celery_app

    celery_app.start(argv=["beat", "-l", "info"])
