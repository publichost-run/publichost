"""asyncssh-based SSH server.

Features: passwordless login, one-time subdomain assignment per connection,
banner session showing the assigned address, remote port forwarding
(`ssh -R 0:localhost:<port>`) so the HTTP reverse proxy can reach the
client's localhost, and a 2-hour tunnel lifetime with an `extend` command.

Security: the service never grants shell/exec access to clients — the only
session type is `session.BannerSession`. `server_requested` (remote port
forwarding) is the sole other accepted request. Any new SSH request handler
(sftp, direct-tcpip, exec, ...) must be explicitly reviewed before being added.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import asyncssh

from ..common.events import EventPublisher
from .config import Settings
from .registry import Registry, SubdomainPoolExhaustedError
from .session import BannerSession

logger = logging.getLogger(__name__)

# Bind address for remote-forward listeners. The HTTP proxy always reaches the
# tunnel backend via loopback, so the client-supplied listen host is ignored to
# prevent binding an open relay on the server's public interfaces.
_LOOPBACK = "127.0.0.1"


class ConnectionTracker:
    """Counts concurrent raw connections in total and per source IP.

    Shared across all per-connection ``PublicHostSSHServer`` instances so that
    connection-rate limits can be enforced before any resource is allocated.
    """

    def __init__(self) -> None:
        self._per_ip: dict[str, int] = {}
        self._total = 0

    @property
    def total(self) -> int:
        return self._total

    def count_for(self, ip: str) -> int:
        return self._per_ip.get(ip, 0)

    def add(self, ip: str) -> None:
        self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
        self._total += 1

    def remove(self, ip: str) -> None:
        current = self._per_ip.get(ip, 0)
        if current <= 1:
            self._per_ip.pop(ip, None)
        else:
            self._per_ip[ip] = current - 1
        if self._total > 0:
            self._total -= 1


class PublicHostSSHServer(asyncssh.SSHServer):
    """Handles one client connection: assigns and releases its subdomain."""

    def __init__(
        self,
        registry: Registry,
        settings: Settings | None = None,
        publisher: EventPublisher | None = None,
        *,
        connections: dict[str, asyncssh.SSHServerConnection] | None = None,
        tracker: ConnectionTracker | None = None,
    ) -> None:
        self._registry = registry
        self._settings = settings or Settings()
        self._publisher = publisher
        self._connections = connections
        self._tracker = tracker or ConnectionTracker()
        self._domain: str | None = None
        self._token: str | None = None
        self._conn: asyncssh.SSHServerConnection | None = None
        self._forwarding_bound = False
        self._session: BannerSession | None = None
        self._peer_ip: str | None = None
        self._counted = False

    def _publish_tunnel(self, event: str, **kwargs: Any) -> None:
        if self._publisher is None or self._domain is None:
            return
        asyncio.create_task(
            self._publisher.publish_tunnel(
                event, self._domain, token=self._token, **kwargs
            )
        )

    def begin_auth(self, username: str) -> bool:
        # Passwordless service: anyone may connect, identity is the subdomain.
        logger.info("Client connected, username=%r (no auth required)", username)
        return False

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        # Fires on raw TCP accept, before any SSH handshake or authentication.
        # Only enforce connection-rate limits here; never allocate resources
        # (subdomain/token/events) until the client is authenticated.
        self._conn = conn
        peername = conn.get_extra_info("peername")
        self._peer_ip = peername[0] if peername else "unknown"
        if self._tracker.total >= self._settings.max_total_connections:
            logger.warning(
                "Total connection limit (%d) reached, rejecting %s",
                self._settings.max_total_connections,
                self._peer_ip,
            )
            conn.close()
            return
        if self._tracker.count_for(self._peer_ip) >= (
            self._settings.max_connections_per_ip
        ):
            logger.warning(
                "Per-IP connection limit (%d) reached for %s, rejecting",
                self._settings.max_connections_per_ip,
                self._peer_ip,
            )
            conn.close()
            return
        self._tracker.add(self._peer_ip)
        self._counted = True

    def auth_completed(self) -> None:
        # Fires only after the SSH connection is authenticated. Allocate the
        # subdomain/token and publish the open event here so that anonymous raw
        # TCP connects cannot exhaust the registry or flood the event bus.
        if self._conn is None:
            return
        try:
            self._domain, self._token = self._registry.acquire()
        except SubdomainPoolExhaustedError:
            logger.error("Subdomain pool exhausted, dropping client")
            self._conn.close()
            return
        logger.info("Assigned %s", self._domain)
        if self._connections is not None:
            self._connections[self._domain] = self._conn
        info = self._registry.get_info(self._domain)
        self._publish_tunnel(
            "tunnel.opened",
            base_domain=self._settings.base_domain,
            ssh_domain=self._settings.ssh_domain,
            frontend_domain=self._settings.frontend_domain,
            created_at=info.created_at.isoformat() if info else None,
            expires_at=info.expires_at.isoformat() if info else None,
        )

    def connection_lost(self, exc: Exception | None) -> None:
        if self._counted and self._peer_ip is not None:
            self._tracker.remove(self._peer_ip)
            self._counted = False
        if self._domain is not None:
            reason = "expired" if self._is_expired() else "closed"
            self._publish_tunnel(f"tunnel.{reason}")
            if self._session is not None:
                self._session.write_goodbye(reason)
                self._session = None
            if self._connections is not None:
                self._connections.pop(self._domain, None)
            self._registry.release(self._domain)
            logger.info("Released %s (%s)", self._domain, reason)
            self._domain = None
        self._token = None
        self._conn = None

    def _is_expired(self) -> bool:
        if self._domain is None:
            return False
        info = self._registry.get_info(self._domain)
        if info is None:
            return False
        return info.expires_at <= datetime.now(UTC)

    def session_requested(self) -> asyncssh.SSHServerSession | bool:
        if self._domain is None or self._token is None:
            return False
        self._session = BannerSession(
            self._domain,
            self._token,
            self._registry,
            self._settings,
            self._publisher,
        )
        return self._session

    async def server_requested(
        self, listen_host: str, listen_port: int
    ) -> asyncssh.SSHListener | bool:
        """Accept an ephemeral remote forwarding request from the client."""
        if self._domain is None or self._conn is None:
            return False
        if self._forwarding_bound:
            logger.info("Only one remote forward per connection is allowed")
            return False
        if listen_port != 0:
            logger.info("Only ephemeral remote forwards are supported")
            return False

        # Force the server-side bind (first arg) to loopback and ignore the
        # attacker-controlled ``listen_host``. The proxy reaches the backend via
        # 127.0.0.1, so binding a public interface would only create an open TCP
        # relay. ``listen_host`` is still passed as the forwarded-tcpip connected
        # address (dest) so the client can match the channel to its own forward.
        try:
            listener = await self._conn.forward_local_port(_LOOPBACK, 0, listen_host, 0)
        except OSError as exc:
            logger.warning("Failed to create tunnel listener: %s", exc)
            return False

        port = listener.get_port()
        self._registry.bind_listener(self._domain, port)
        self._forwarding_bound = True
        logger.info(
            "Tunnel for %s -> %s:%d -> client localhost",
            self._domain,
            _LOOPBACK,
            port,
        )
        return listener


async def close_expired_tunnels(
    registry: Registry,
    connections: dict[str, asyncssh.SSHServerConnection],
    interval_seconds: int,
    publisher: EventPublisher | None = None,
) -> None:
    """Background task that periodically closes expired SSH connections."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            expired = registry.get_expired()
        except Exception:
            logger.exception("Failed to scan for expired tunnels")
            continue
        for domain in expired:
            # Isolate per-tunnel failures (e.g. a publish error) so one bad
            # tunnel can neither skip the others nor kill the whole loop.
            try:
                info = registry.get_info(domain)
                if info is not None and info.status not in ("expired", "closed"):
                    registry.set_status(domain, "expired")
                    if publisher is not None:
                        await publisher.publish_tunnel("tunnel.expired", domain)
                conn = connections.get(domain)
                if conn is not None:
                    logger.info("Closing expired tunnel %s", domain)
                    conn.close()
            except Exception:
                logger.exception("Failed to close expired tunnel %s", domain)
