"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .consumer import consume_redis
from .db import engine
from .models import Base
from .routers import weblogs


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
    await engine.dispose()


app = FastAPI(title="PublicHost API", lifespan=lifespan)
app.include_router(weblogs.router)
