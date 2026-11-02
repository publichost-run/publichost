"""Tunnel service composition: SSH server + HTTP reverse proxy + expiry monitor."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import NoReturn

import asyncssh
from dotenv import load_dotenv

from ..common.events import EventPublisher
from .config import Settings
from .heartbeat import run_heartbeat
from .hostkey import ensure_host_key
from .proxy import HttpProxy
from .registry import Registry
from .server import (
    ConnectionTracker,
    PublicHostSSHServer,
    TunnelTracker,
    close_expired_tunnels,
)

logger = logging.getLogger(__name__)


def _server_factory(
    registry: Registry,
    settings: Settings,
    connections: dict[str, asyncssh.SSHServerConnection],
    publisher: EventPublisher,
    tracker: ConnectionTracker,
    tunnel_tracker: TunnelTracker,
) -> PublicHostSSHServer:
    """Factory that injects shared registry, connection tracker and publisher.

    The subdomain -> connection registration and connection-rate accounting
    live in ``PublicHostSSHServer`` itself now, so the shared ``connections``
    map and ``tracker`` are passed straight through.
    """
    return PublicHostSSHServer(
        registry,
        settings,
        publisher,
        connections=connections,
        tracker=tracker,
        tunnel_tracker=tunnel_tracker,
    )


async def run_server(
    registry: Registry,
    settings: Settings,
    connections: dict[str, asyncssh.SSHServerConnection],
    publisher: EventPublisher,
) -> None:
    """Start the SSH server and run it forever."""
    host_key = ensure_host_key(settings.host_key_path)
    tracker = ConnectionTracker()
    tunnel_tracker = TunnelTracker()
    await asyncssh.create_server(
        lambda: _server_factory(
            registry, settings, connections, publisher, tracker, tunnel_tracker
        ),
        settings.ssh_host,
        settings.ssh_port,
        server_host_keys=[host_key],
    )
    logger.info(
        "SSH server listening on %s:%d, handing out *.%s (ssh host: %s)",
        settings.ssh_host,
        settings.ssh_port,
        settings.base_domain,
        settings.ssh_domain,
    )
    await asyncio.Future()  # run forever


async def run_proxy(
    registry: Registry, settings: Settings, publisher: EventPublisher
) -> NoReturn:
    """Start the HTTP reverse proxy and run it forever."""
    proxy = HttpProxy(
        registry,
        settings.http_host,
        settings.http_port,
        publisher,
        max_header_bytes=settings.max_header_bytes,
    )
    await proxy.run()


async def _run(settings: Settings) -> None:
    """Start SSH server, HTTP proxy, and expiry monitor concurrently."""
    publisher = EventPublisher(settings.redis_url)
    await publisher.connect()
    try:
        registry = Registry(settings.base_domain, settings.default_lifetime_minutes)
        connections: dict[str, asyncssh.SSHServerConnection] = {}
        instance = settings.instance_id or socket.gethostname()
        # return_exceptions=True so that a failure in one long-running task
        # (e.g. the expiry monitor) does not cancel the SSH server and HTTP
        # proxy along with it.
        results = await asyncio.gather(
            run_server(registry, settings, connections, publisher),
            run_proxy(registry, settings, publisher),
            close_expired_tunnels(
                registry,
                connections,
                settings.expire_check_interval_seconds,
                publisher,
            ),
            run_heartbeat(
                registry,
                publisher,
                settings.heartbeat_interval_seconds,
                instance,
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.error("Service task exited with error: %r", result)
    finally:
        await publisher.close()


def main() -> None:
    # Make local development convenient: load .env if it exists.
    # Production deployments (Docker, systemd, etc.) set real env vars.
    if os.environ.get("PUBLICHOST_NO_DOTENV") != "1":
        load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        logger.info("Shutting down")


if __name__ == "__main__":
    main()
