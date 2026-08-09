"""Redis key contract for public stats aggregation (shared by tunnel + api)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

REQUEST_BUCKET_PREFIX = "publichost:stats:requests"
REQUEST_BUCKET_TTL_SECONDS = 90_000  # 25h, so a 24h window is always covered
RELAY_HEARTBEAT_KEY = "publichost:relay:heartbeat"
RELAY_HEARTBEAT_TTL_SECONDS = 300
RELAY_OPERATIONAL_MAX_AGE_SECONDS = 30


def request_bucket_key(moment: datetime) -> str:
    """Redis key for the hourly request-count bucket containing `moment`."""
    hour = moment.astimezone(UTC).strftime("%Y-%m-%dT%H")
    return f"{REQUEST_BUCKET_PREFIX}:{hour}"


def last_24h_bucket_keys(now: datetime) -> list[str]:
    """The 24 hourly bucket keys covering the current UTC hour and the previous 23."""
    now_utc = now.astimezone(UTC)
    return [request_bucket_key(now_utc - timedelta(hours=i)) for i in range(24)]
