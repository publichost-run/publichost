"""Tests for the periodic relay heartbeat background task."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from publichost.tunnel.heartbeat import run_heartbeat


async def wait_for(predicate: Callable[[], bool], timeout_s: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


class _FakeRegistry:
    def __init__(self, count: int) -> None:
        self._count = count

    @property
    def active_tunnel_count(self) -> int:
        return self._count


class _RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    async def write_relay_heartbeat(self, active_tunnels: int, instance: str) -> None:
        self.calls.append((active_tunnels, instance))


class _FlakyPublisher:
    def __init__(self) -> None:
        self.calls = 0

    async def write_relay_heartbeat(self, active_tunnels: int, instance: str) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")


async def test_run_heartbeat_reports_count_and_instance():
    registry = _FakeRegistry(4)
    publisher = _RecordingPublisher()
    task = asyncio.create_task(run_heartbeat(registry, publisher, 0, "relay-x"))
    try:
        assert await wait_for(lambda: len(publisher.calls) >= 1)
        assert publisher.calls[0] == (4, "relay-x")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_run_heartbeat_survives_publish_error():
    registry = _FakeRegistry(1)
    publisher = _FlakyPublisher()
    task = asyncio.create_task(run_heartbeat(registry, publisher, 0, "relay-x"))
    try:
        # The first call raises; the loop must keep going and call again.
        assert await wait_for(lambda: publisher.calls >= 2)
        await asyncio.sleep(0.05)
        assert not task.done()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
