"""FastAPI backend settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class ApiSettings(BaseSettings):
    """Settings for the FastAPI backend.

    Environment variables:
        PUBLICHOST_DATABASE_URL: async PostgreSQL URL.
        PUBLICHOST_REDIS_URL: Redis connection URL.
        PUBLICHOST_WEBLOGS_PATH: directory where WebLogs CSV files are stored.
        PUBLICHOST_API_HOST: interface the API server binds to.
        PUBLICHOST_API_PORT: port the API server binds to.
    """

    database_url: str = (
        "postgresql+asyncpg://publichost:publichost@localhost:5432/publichost"
    )
    redis_url: str = "redis://localhost:6379/0"
    weblogs_path: str = "weblogs"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    class Config:
        env_prefix = "PUBLICHOST_"
        env_file = ".env"
        extra = "ignore"


settings = ApiSettings()
