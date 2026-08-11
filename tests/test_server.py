import asyncio
import contextlib
import os
import re
import stat
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import asyncssh
import pytest

from publichost.tunnel.config import Settings
from publichost.tunnel.hostkey import ensure_host_key
from publichost.tunnel.registry import Registry
from publichost.tunnel.server import (
    ConnectionTracker,
    PublicHostSSHServer,
    close_expired_tunnels,
)

_DOMAIN_RE = re.compile(r"[a-z]+-[a-z]+-\d+\.publichost\.test")


@asynccontextmanager
async def running_server(
    registry: Registry,
    factory: Callable[[], PublicHostSSHServer] | None = None,
) -> AsyncIterator[asyncssh.SSHAcceptor]:
    if factory is None:

        def factory() -> PublicHostSSHServer:
            return PublicHostSSHServer(registry)

    server = await asyncssh.create_server(
        factory,
        "127.0.0.1",
        0,
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
    )
    try:
        yield server
    finally:
        server.close()
        await server.wait_closed()


async def wait_for(predicate: Callable[[], bool], timeout_s: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def test_client_is_assigned_a_subdomain():
    registry = Registry("publichost.test")
    async with running_server(registry) as server:
        port = server.get_port()
        async with asyncssh.connect("127.0.0.1", port, known_hosts=None) as conn:
            proc = await conn.create_process()
            banner = await asyncio.wait_for(proc.stdout.read(2048), timeout=2.0)
            assert _DOMAIN_RE.search(banner), f"unexpected banner: {banner!r}"
            assert await wait_for(lambda: len(registry) == 1)


async def test_ctrl_c_over_a_real_pty_closes_the_tunnel():
    """Every real interactive ssh client requests a PTY, which makes asyncssh
    wrap the session in its built-in line editor. The line editor intercepts
    Ctrl+C (0x03) itself and calls session.break_received() instead of ever
    forwarding the raw byte through data_received() -- so a fix that only
    patches data_received (as verified by tests that call the session object
    directly, bypassing asyncssh's real client/server stack and PTY handling
    entirely) never actually reaches a real user pressing Ctrl+C.

    This drives a real asyncssh client and server end to end with a PTY
    requested, exactly like a real terminal does, to catch that gap.
    """
    registry = Registry("publichost.test")
    async with running_server(registry) as server:
        port = server.get_port()
        async with asyncssh.connect("127.0.0.1", port, known_hosts=None) as conn:
            proc = await conn.create_process(term_type="ansi")
            banner = await asyncio.wait_for(proc.stdout.read(4096), timeout=2.0)
            assert "Ctrl+C" in banner

            proc.stdin.write("\x03")
            await proc.stdin.drain()

            # The session channel must close on its own -- this is the actual
            # bug: break_received previously did nothing, so this hung until
            # timeout instead.
            await asyncio.wait_for(proc.wait_closed(), timeout=2.0)

            # A real ssh client disconnects once its only channel is gone;
            # asyncssh's client library leaves that to the caller, so do it
            # explicitly to reach the same connection_lost -> registry
            # release the CLI's real disconnect would trigger.
            conn.close()
            await conn.wait_closed()
            assert await wait_for(lambda: len(registry) == 0)


async def test_subdomain_is_released_on_disconnect():
    registry = Registry("publichost.test")
    async with running_server(registry) as server:
        port = server.get_port()
        conn = await asyncssh.connect("127.0.0.1", port, known_hosts=None)
        assert await wait_for(lambda: len(registry) == 1)
        conn.close()
        await conn.wait_closed()
        assert await wait_for(lambda: len(registry) == 0)


async def test_no_subdomain_allocated_before_authentication():
    # Fix 2: a raw TCP connect that never completes the SSH handshake must not
    # allocate a subdomain (previously connection_made allocated one).
    registry = Registry("publichost.test")
    async with running_server(registry) as server:
        port = server.get_port()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            # Send no SSH bytes at all, so auth never completes.
            await asyncio.sleep(0.3)
            assert len(registry) == 0
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


async def test_per_ip_connection_limit_rejects_excess():
    # Fix 2: concurrent connections from one IP beyond the limit are rejected
    # at accept time, before any resource is allocated.
    registry = Registry("publichost.test")
    settings = Settings(
        base_domain="publichost.test",
        max_connections_per_ip=2,
        max_total_connections=100,
    )
    tracker = ConnectionTracker()

    def factory() -> PublicHostSSHServer:
        return PublicHostSSHServer(registry, settings, tracker=tracker)

    async with running_server(registry, factory) as server:
        port = server.get_port()
        conns = [
            await asyncssh.connect("127.0.0.1", port, known_hosts=None)
            for _ in range(2)
        ]
        try:
            with pytest.raises((asyncssh.Error, OSError)):
                await asyncssh.connect("127.0.0.1", port, known_hosts=None)
            assert tracker.total == 2
        finally:
            for conn in conns:
                conn.close()
                await conn.wait_closed()


async def test_total_connection_limit_rejects_excess():
    # Fix 2: server-wide concurrent connection cap is enforced.
    registry = Registry("publichost.test")
    settings = Settings(
        base_domain="publichost.test",
        max_connections_per_ip=100,
        max_total_connections=1,
    )
    tracker = ConnectionTracker()

    def factory() -> PublicHostSSHServer:
        return PublicHostSSHServer(registry, settings, tracker=tracker)

    async with running_server(registry, factory) as server:
        port = server.get_port()
        conn = await asyncssh.connect("127.0.0.1", port, known_hosts=None)
        try:
            with pytest.raises((asyncssh.Error, OSError)):
                await asyncssh.connect("127.0.0.1", port, known_hosts=None)
        finally:
            conn.close()
            await conn.wait_closed()


class _RecordingConn:
    """Stand-in for an SSHServerConnection that records forward requests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str, int]] = []

    async def forward_local_port(
        self, listen_host: str, listen_port: int, dest_host: str, dest_port: int
    ) -> "_FakeListener":
        self.calls.append((listen_host, listen_port, dest_host, dest_port))
        return _FakeListener()


class _FakeListener:
    def get_port(self) -> int:
        return 54321


async def test_remote_forward_always_binds_loopback():
    # Fix 1: the client-supplied listen host is ignored; the bind is loopback.
    registry = Registry("publichost.test")
    server = PublicHostSSHServer(registry)
    domain, token = registry.acquire()
    server._domain = domain
    server._token = token
    fake_conn = _RecordingConn()
    server._conn = fake_conn  # type: ignore[assignment]

    listener = await server.server_requested("0.0.0.0", 0)

    assert listener is not None
    # The server-side bind host (first arg) is forced to loopback regardless of
    # the client-supplied "0.0.0.0"; the client value is only echoed back as the
    # forwarded-tcpip connected address (third arg) for channel matching.
    assert len(fake_conn.calls) == 1
    bind_host = fake_conn.calls[0][0]
    assert bind_host == "127.0.0.1"


class _BoomPublisher:
    async def publish_tunnel(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")


async def test_expiry_loop_survives_publish_error():
    # Fix 3: an exception while publishing must not kill the expiry loop, and
    # the expired tunnel is still eventually closed.
    registry = Registry("publichost.test", default_lifetime_minutes=0)
    domain, _ = registry.acquire()
    closed: list[str] = []

    class _FakeConn:
        def close(self) -> None:
            closed.append(domain)

    connections = {domain: _FakeConn()}
    task = asyncio.create_task(
        close_expired_tunnels(registry, connections, 0, _BoomPublisher())
    )
    try:
        assert await wait_for(lambda: domain in closed)
        await asyncio.sleep(0.05)
        assert not task.done()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_host_key_created_with_restrictive_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fix 4: the host key file must be created already restricted to 0600,
    # not created world-readable and chmod'd afterwards.
    def _no_chmod(*args: object, **kwargs: object) -> None:
        raise AssertionError("chmod must not be relied upon for permissions")

    monkeypatch.setattr(Path, "chmod", _no_chmod)
    old_umask = os.umask(0)
    try:
        key_path = tmp_path / "host_key"
        result = ensure_host_key(str(key_path))
        assert result == str(key_path)
        mode = stat.S_IMODE(os.stat(key_path).st_mode)
        assert mode == 0o600
    finally:
        os.umask(old_umask)
