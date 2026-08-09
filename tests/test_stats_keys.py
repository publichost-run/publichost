"""Tests for the shared Redis stats key contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from publichost.common.stats import last_24h_bucket_keys, request_bucket_key


def test_request_bucket_key_is_utc_normalized() -> None:
    utc_aware = datetime(2026, 8, 9, 12, 30, tzinfo=UTC)
    # Same instant expressed in a +02:00 offset.
    other_zone = datetime(2026, 8, 9, 14, 30, tzinfo=timezone(timedelta(hours=2)))
    assert request_bucket_key(utc_aware) == request_bucket_key(other_zone)


def test_request_bucket_key_hour_precision() -> None:
    a = datetime(2026, 8, 9, 12, 5, tzinfo=UTC)
    b = datetime(2026, 8, 9, 12, 15, tzinfo=UTC)
    assert request_bucket_key(a) == request_bucket_key(b)

    before = datetime(2026, 8, 9, 12, 59, tzinfo=UTC)
    after = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    assert request_bucket_key(before) != request_bucket_key(after)


def test_last_24h_bucket_keys() -> None:
    now = datetime(2026, 8, 9, 12, 30, tzinfo=UTC)
    keys = last_24h_bucket_keys(now)
    assert len(keys) == 24
    assert keys[0] == request_bucket_key(now)
    assert len(set(keys)) == 24
