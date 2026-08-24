"""Runtime settings for the tunnel service (SSH server + HTTP proxy)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..common.config import CommonSettings


@dataclass(frozen=True)
class Settings(CommonSettings):
    """Tunnel service configuration.

    Environment variables:
        PUBLICHOST_BASE_DOMAIN: base domain used for tunnel subdomains.
        PUBLICHOST_SSH_DOMAIN: domain clients use for SSH connections.
        PUBLICHOST_FRONTEND_DOMAIN: domain serving the public web frontend.
        PUBLICHOST_SSH_HOST: interface the SSH server binds to.
        PUBLICHOST_SSH_PORT: port the SSH server binds to.
        PUBLICHOST_HOST_KEY_PATH: path to the SSH host key (created on first run).
        PUBLICHOST_HTTP_HOST: interface the HTTP reverse proxy binds to.
        PUBLICHOST_HTTP_PORT: port the HTTP reverse proxy binds to.
        PUBLICHOST_DEFAULT_LIFETIME_MINUTES: default tunnel lifetime in minutes.
        PUBLICHOST_EXPIRE_CHECK_INTERVAL_SECONDS: interval for scanning expired tunnels.
        PUBLICHOST_WEBLOGS_PATH: directory to store WebLogs CSV files.
        PUBLICHOST_REDIS_URL: Redis connection URL.
        PUBLICHOST_MAX_CONNECTIONS_PER_IP: max concurrent raw SSH connections per
            client IP.
        PUBLICHOST_MAX_TOTAL_CONNECTIONS: max concurrent raw SSH connections
            server-wide.
        PUBLICHOST_MAX_TUNNELS_PER_IP: max concurrent active tunnels per client
            IP (checked when a remote forward is requested). Clamped to a
            minimum of 1.
        PUBLICHOST_MAX_HEADER_BYTES: max bytes allowed for a single HTTP
            request/response header block read by the proxy.
        PUBLICHOST_MAX_EXTEND_MINUTES: max minutes a single `extend` command may add.
        PUBLICHOST_MAX_TOTAL_LIFETIME_MINUTES: max total lifetime (initial + all
            extends) a tunnel may reach.
        PUBLICHOST_HEARTBEAT_INTERVAL_SECONDS: interval for publishing the relay
            liveness heartbeat.
        PUBLICHOST_INSTANCE_ID: identifier for this relay instance in heartbeats
            (empty string resolves to the hostname at the call site).
    """

    base_domain: str = "publichost.run"
    ssh_domain: str = "publichost.run"
    frontend_domain: str = "publichost.io"
    ssh_host: str = "0.0.0.0"
    ssh_port: int = 2222
    host_key_path: str = "host_key"
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    default_lifetime_minutes: int = 120
    expire_check_interval_seconds: int = 60
    max_connections_per_ip: int = 10
    max_total_connections: int = 500
    max_tunnels_per_ip: int = 2
    max_header_bytes: int = 16384
    max_extend_minutes: int = 120
    max_total_lifetime_minutes: int = 1440
    heartbeat_interval_seconds: int = 10
    instance_id: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from PUBLICHOST_* environment variables."""
        common = CommonSettings._from_env()
        return cls(
            redis_url=common.redis_url,
            weblogs_path=common.weblogs_path,
            base_domain=os.environ.get("PUBLICHOST_BASE_DOMAIN", cls.base_domain),
            ssh_domain=os.environ.get("PUBLICHOST_SSH_DOMAIN", cls.ssh_domain),
            frontend_domain=os.environ.get(
                "PUBLICHOST_FRONTEND_DOMAIN", cls.frontend_domain
            ),
            ssh_host=os.environ.get("PUBLICHOST_SSH_HOST", cls.ssh_host),
            ssh_port=int(os.environ.get("PUBLICHOST_SSH_PORT", cls.ssh_port)),
            host_key_path=os.environ.get("PUBLICHOST_HOST_KEY_PATH", cls.host_key_path),
            http_host=os.environ.get("PUBLICHOST_HTTP_HOST", cls.http_host),
            http_port=int(os.environ.get("PUBLICHOST_HTTP_PORT", cls.http_port)),
            default_lifetime_minutes=int(
                os.environ.get(
                    "PUBLICHOST_DEFAULT_LIFETIME_MINUTES",
                    cls.default_lifetime_minutes,
                )
            ),
            expire_check_interval_seconds=int(
                os.environ.get(
                    "PUBLICHOST_EXPIRE_CHECK_INTERVAL_SECONDS",
                    cls.expire_check_interval_seconds,
                )
            ),
            max_connections_per_ip=int(
                os.environ.get(
                    "PUBLICHOST_MAX_CONNECTIONS_PER_IP", cls.max_connections_per_ip
                )
            ),
            max_total_connections=int(
                os.environ.get(
                    "PUBLICHOST_MAX_TOTAL_CONNECTIONS", cls.max_total_connections
                )
            ),
            # Clamp to a sane minimum: a 0/negative value would silently reject
            # every remote forward server-wide while SSH login itself still
            # succeeds -- a confusing failure mode.
            max_tunnels_per_ip=max(
                1,
                int(
                    os.environ.get(
                        "PUBLICHOST_MAX_TUNNELS_PER_IP", cls.max_tunnels_per_ip
                    )
                ),
            ),
            max_header_bytes=int(
                os.environ.get("PUBLICHOST_MAX_HEADER_BYTES", cls.max_header_bytes)
            ),
            max_extend_minutes=int(
                os.environ.get("PUBLICHOST_MAX_EXTEND_MINUTES", cls.max_extend_minutes)
            ),
            max_total_lifetime_minutes=int(
                os.environ.get(
                    "PUBLICHOST_MAX_TOTAL_LIFETIME_MINUTES",
                    cls.max_total_lifetime_minutes,
                )
            ),
            # Clamp to a sane minimum so a misconfigured 0/negative value cannot
            # turn run_heartbeat's loop into a tight Redis-hammering busy-loop.
            heartbeat_interval_seconds=max(
                1,
                int(
                    os.environ.get(
                        "PUBLICHOST_HEARTBEAT_INTERVAL_SECONDS",
                        cls.heartbeat_interval_seconds,
                    )
                ),
            ),
            instance_id=os.environ.get("PUBLICHOST_INSTANCE_ID", cls.instance_id),
        )
