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
        PUBLICHOST_CLI_LATEST_VERSION: newest published CLI version.
        PUBLICHOST_CLI_MIN_SUPPORTED_VERSION: oldest CLI version still accepted.
        PUBLICHOST_CLI_RELEASED_AT: release date of the latest CLI version.
        PUBLICHOST_CLI_RELEASE_URL: URL of the latest CLI release page.
        PUBLICHOST_CLI_INSTALL_COMMAND: shell command that installs the CLI.
        PUBLICHOST_STATS_CACHE_SECONDS: TTL for the /api/v1/stats snapshot cache.
        PUBLICHOST_CORS_ORIGINS: comma-separated list of allowed CORS origins.
    """

    database_url: str = (
        "postgresql+asyncpg://publichost:publichost@localhost:5432/publichost"
    )
    redis_url: str = "redis://localhost:6379/0"
    weblogs_path: str = "weblogs"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cli_latest_version: str = "1.0.0"
    cli_min_supported_version: str = "1.0.0"
    cli_released_at: str = ""
    cli_release_url: str = ""
    cli_install_command: str = ""
    stats_cache_seconds: int = 5
    cors_origins: str = "https://publichost.io,https://www.publichost.io"

    class Config:
        env_prefix = "PUBLICHOST_"
        env_file = ".env"
        extra = "ignore"


settings = ApiSettings()
