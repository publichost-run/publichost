"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .consumer import consume_redis
from .db import engine
from .models import Base
from .routers import cli_release, stats, weblogs


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables and start Redis consumer on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    consumer_task = asyncio.create_task(consume_redis())
    yield
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await stats.close_redis()
    await engine.dispose()


app = FastAPI(title="PublicHost API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["GET"],
    allow_credentials=False,
)
app.include_router(weblogs.router)
app.include_router(stats.router)
app.include_router(cli_release.router)
