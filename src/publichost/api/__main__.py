"""Run the FastAPI backend with uvicorn."""

from __future__ import annotations

import uvicorn

from .config import settings

if __name__ == "__main__":
    uvicorn.run(
        "publichost.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
