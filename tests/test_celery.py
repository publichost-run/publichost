"""Regression tests for Celery cleanup, the token uniqueness constraint, and
timezone-aware release timestamps."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from publichost.api import consumer
from publichost.api.celery import tasks
from publichost.api.models import Base, Tunnel

_REAL_DB_URL = os.environ.get(
    "PUBLICHOST_TEST_DATABASE_URL",
    "postgresql+asyncpg://publichost:publichost@127.0.0.1:55432/publichost",
)


def _real_db_available(url: str) -> bool:
    async def _probe() -> bool:
        engine = create_async_engine(url)
        try:
            async with engine.connect():
                return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    return asyncio.run(_probe())


class _FakeResult:
    def __init__(self, tunnels: list[Tunnel]) -> None:
        self._tunnels = tunnels

    def scalars(self):
        return list(self._tunnels)

    def scalar_one_or_none(self):
        return self._tunnels[0] if self._tunnels else None


class _FakeSession:
    """Minimal async-session stand-in that records that it was exercised."""

    def __init__(self, tunnels: list[Tunnel]) -> None:
        self._tunnels = tunnels
        self.committed = False
        self.executed = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def execute(self, _stmt) -> _FakeResult:
        self.executed = True
        return _FakeResult(self._tunnels)

    async def commit(self) -> None:
        self.committed = True

    def add(self, _obj) -> None:  # pragma: no cover - not used here
        pass


def _make_tunnel(**overrides) -> Tunnel:
    now = datetime.now(UTC)
    defaults = dict(
        subdomain="brave-fox-1",
        token="tok-123",
        domain="brave-fox-1.publichost.run",
        ssh_domain="ssh.publichost.run",
        status="opened",
        created_at=now,
        expires_at=now - timedelta(minutes=5),
    )
    defaults.update(overrides)
    return Tunnel(**defaults)


# --- Fix 1: Celery task must actually execute its async body ------------------


def test_cleanup_task_run_executes_async_body(monkeypatch):
    """`.run()` (how a Celery worker invokes the task) must run the cleanup.

    Against the previous `async def` task, `.run()` returned an unawaited
    coroutine and the body never executed, so the tunnel stayed "opened" and
    the session was never committed.
    """
    expired = _make_tunnel(status="opened")
    session = _FakeSession([expired])
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: session)

    result = tasks.cleanup_expired_tunnels.run()

    assert result == 1
    assert session.executed is True
    assert session.committed is True
    assert expired.status == "expired"
    assert expired.released_at is not None
    assert expired.released_at.tzinfo is not None


@pytest.mark.skipif(
    not _real_db_available(_REAL_DB_URL),
    reason="requires a real Postgres reachable at PUBLICHOST_TEST_DATABASE_URL",
)
def test_cleanup_task_survives_repeated_invocations(monkeypatch):
    """The task must keep working across repeated worker-style calls.

    `.run()` wraps the async body in `asyncio.run()`, which tears down its
    event loop on return. The `_FakeSession`-based test above never exercises
    a real asyncpg connection pool, so it can't catch that the pool's
    connections stay bound to that now-closed loop: a second `.run()` used to
    fail with "Future ... attached to a different loop". The fix disposes
    the engine at the end of each run so the next call reconnects cleanly.
    """
    test_engine = create_async_engine(_REAL_DB_URL)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "engine", test_engine)
    monkeypatch.setattr(tasks, "AsyncSessionLocal", test_session_factory)

    async def _create_schema_and_seed(token: str) -> None:
        # Each helper call runs in its own asyncio.run() loop, same as the
        # task itself, so it must dispose the pool before returning too —
        # otherwise a connection cached from *this* loop leaks into the next
        # asyncio.run() and reproduces the very bug under test.
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with test_session_factory() as session:
                session.add(_make_tunnel(subdomain=token, token=token))
                await session.commit()
        finally:
            await test_engine.dispose()

    async def _teardown() -> None:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await test_engine.dispose()

    try:
        asyncio.run(_create_schema_and_seed("loop-tick-1"))
        assert tasks.cleanup_expired_tunnels.run() == 1

        asyncio.run(_create_schema_and_seed("loop-tick-2"))
        assert tasks.cleanup_expired_tunnels.run() == 1

        asyncio.run(_create_schema_and_seed("loop-tick-3"))
        assert tasks.cleanup_expired_tunnels.run() == 1
    finally:
        asyncio.run(_teardown())


# --- Fix 2: token column must be UNIQUE ---------------------------------------


def test_duplicate_token_raises_integrity_error():
    """Two tunnels sharing a token must be rejected by the DB constraint."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(_make_tunnel(subdomain="a", token="dup"))
            session.commit()
            session.add(_make_tunnel(subdomain="b", token="dup"))
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


# --- Fix 3: released_at must be timezone-aware (UTC) --------------------------


async def test_tunnel_closed_sets_timezone_aware_released_at(monkeypatch):
    existing = _make_tunnel(status="opened")
    session = _FakeSession([existing])
    monkeypatch.setattr(consumer, "AsyncSessionLocal", lambda: session)

    await consumer._handle_tunnel_event(
        {"event": "tunnel.closed", "domain": existing.domain, "token": existing.token}
    )

    assert existing.status == "closed"
    assert existing.released_at is not None
    assert existing.released_at.tzinfo is not None
