"""Settings shared by the tunnel service and the API backend."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CommonSettings:
    """Fields both services read from the environment.

    Environment variables:
        PUBLICHOST_REDIS_URL: Redis connection URL (pub/sub between services).
        PUBLICHOST_WEBLOGS_PATH: directory where WebLogs CSV files are stored.
    """

    redis_url: str = "redis://localhost:6379/0"
    weblogs_path: str = "weblogs"

    @classmethod
    def _from_env(cls) -> CommonSettings:
        """Build the common fields from PUBLICHOST_* environment variables."""
        return cls(
            redis_url=os.environ.get("PUBLICHOST_REDIS_URL", cls.redis_url),
            weblogs_path=os.environ.get("PUBLICHOST_WEBLOGS_PATH", cls.weblogs_path),
        )
