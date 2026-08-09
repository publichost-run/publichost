"""Tests for the pipelined request-counter side of EventPublisher.publish_log."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import redis.asyncio as redis

from publichost.common.events import LOGS_CHANNEL, EventPublisher
from publichost.common.stats import REQUEST_BUCKET_TTL_SECONDS, request_bucket_key


class _FakePipeline:
    def __init__(self) -> None:
        self.commands: list[tuple] = []
        self.execute_calls = 0
        self.raise_on_execute = False

    def publish(self, channel: str, data: str) -> None:
        self.commands.append(("publish", channel, data))

    def incr(self, key: str) -> None:
        self.commands.append(("incr", key))

    def expire(self, key: str, ttl: int, nx: bool = False) -> None:
        self.commands.append(("expire", key, ttl, nx))

    async def execute(self) -> None:
        self.execute_calls += 1
        if self.raise_on_execute:
            raise redis.RedisError("boom")


class _FakeRedis:
    def __init__(self) -> None:
        self.pipe = _FakePipeline()

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        assert transaction is False
        return self.pipe


async def _publish(publisher: EventPublisher) -> None:
    await publisher.publish_log(
        domain="fox.publichost.me",
        token="tok",
        method="GET",
        host="fox.publichost.me",
        path="/",
        status_code=200,
        latency_ms=1.234,
        headers={"x": "y"},
        body="",
    )


async def test_publish_log_pipelines_publish_incr_expire():
    publisher = EventPublisher("redis://x")
    fake = _FakeRedis()
    publisher._client = fake  # type: ignore[assignment]

    before = request_bucket_key(datetime.now(UTC))
    await _publish(publisher)
    after = request_bucket_key(datetime.now(UTC))

    pipe = fake.pipe
    assert pipe.execute_calls == 1
    assert len(pipe.commands) == 3
    kind_publish, channel, _data = pipe.commands[0]
    assert kind_publish == "publish"
    assert channel == LOGS_CHANNEL
    kind_incr, incr_key = pipe.commands[1]
    assert kind_incr == "incr"
    # The bucket key is the current hour; tolerate an hour rollover mid-test.
    assert incr_key in (before, after)
    kind_expire, expire_key, ttl, nx = pipe.commands[2]
    assert kind_expire == "expire"
    assert expire_key == incr_key
    assert ttl == REQUEST_BUCKET_TTL_SECONDS
    # TTL is set only on first creation of the bucket key.
    assert nx is True


async def test_publish_log_noop_without_client():
    publisher = EventPublisher("")
    # No client connected -> must be a silent no-op.
    await _publish(publisher)


async def test_publish_log_swallows_redis_error():
    publisher = EventPublisher("redis://x")
    fake = _FakeRedis()
    fake.pipe.raise_on_execute = True
    publisher._client = fake  # type: ignore[assignment]

    # A RedisError from execute() must be caught, not propagated.
    await _publish(publisher)
    assert fake.pipe.execute_calls == 1


async def test_write_relay_heartbeat_sets_key_with_ttl():
    publisher = EventPublisher("redis://x")

    class _FakeSetRedis:
        def __init__(self) -> None:
            self.set_calls: list[tuple] = []

        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.set_calls.append((key, value, ex))

    fake = _FakeSetRedis()
    publisher._client = fake  # type: ignore[assignment]

    await publisher.write_relay_heartbeat(3, "relay-1")

    assert len(fake.set_calls) == 1
    key, value, ex = fake.set_calls[0]
    from publichost.common.stats import (
        RELAY_HEARTBEAT_KEY,
        RELAY_HEARTBEAT_TTL_SECONDS,
    )

    assert key == RELAY_HEARTBEAT_KEY
    assert ex == RELAY_HEARTBEAT_TTL_SECONDS
    import json

    payload = json.loads(value)
    assert payload["active_tunnels"] == 3
    assert payload["instance"] == "relay-1"
    assert "ts" in payload


async def test_write_relay_heartbeat_noop_without_client():
    publisher = EventPublisher("")
    await publisher.write_relay_heartbeat(1, "relay-1")


async def test_write_relay_heartbeat_swallows_redis_error():
    publisher = EventPublisher("redis://x")

    class _BoomRedis:
        async def set(self, *args: object, **kwargs: object) -> None:
            raise redis.RedisError("boom")

    publisher._client = _BoomRedis()  # type: ignore[assignment]
    await publisher.write_relay_heartbeat(1, "relay-1")


@pytest.mark.parametrize("count", [0, 5])
async def test_heartbeat_payload_reports_count(count: int):
    publisher = EventPublisher("redis://x")

    class _FakeSetRedis:
        def __init__(self) -> None:
            self.value: str | None = None

        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.value = value

    fake = _FakeSetRedis()
    publisher._client = fake  # type: ignore[assignment]
    await publisher.write_relay_heartbeat(count, "relay-1")
    import json

    assert json.loads(fake.value)["active_tunnels"] == count
