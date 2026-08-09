"""In-memory registry of one-time subdomains and their tunnels.

A subdomain lives as long as the SSH connection that acquired it
and is released when the connection drops or the tunnel expires.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

_MAX_ATTEMPTS = 100

# Short, easy-to-read words used to build human-friendly tunnel labels.
_ADJECTIVES = [
    "amber",
    "brave",
    "bright",
    "calm",
    "clever",
    "cool",
    "eager",
    "fast",
    "gentle",
    "happy",
    "jolly",
    "kind",
    "lucky",
    "merry",
    "mighty",
    "noble",
    "proud",
    "quick",
    "quiet",
    "swift",
    "wise",
    "witty",
    "young",
    "zesty",
]

_NOUNS = [
    "bird",
    "cat",
    "cloud",
    "deer",
    "dog",
    "dove",
    "eagle",
    "elm",
    "fox",
    "hawk",
    "lake",
    "leaf",
    "lion",
    "maple",
    "moon",
    "oak",
    "owl",
    "panda",
    "pine",
    "rock",
    "sea",
    "star",
    "storm",
    "tiger",
    "wave",
    "wolf",
]

_TunnelStatus = str


def _generate_label() -> str:
    """Generate a human-readable label: adjective-noun-number."""
    adjective = secrets.choice(_ADJECTIVES)
    noun = secrets.choice(_NOUNS)
    number = secrets.randbelow(9900) + 100  # 100–9999
    return f"{adjective}-{noun}-{number}"


def _generate_token() -> str:
    """Generate a URL-safe secret token for WebLogs access."""
    return secrets.token_urlsafe(32)


class SubdomainPoolExhaustedError(RuntimeError):
    """Raised when no free subdomain can be found."""


class ExtendLimitError(ValueError):
    """Raised when an `extend` request exceeds the configured limits.

    Covers a single extension larger than the allowed maximum, a request that
    would push the tunnel's total lifetime past its cap, and datetime overflow.
    """


@dataclass(frozen=True)
class TunnelInfo:
    """Server-side listener port that forwards to the client's localhost."""

    listen_port: int | None = None
    token: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: _TunnelStatus = "opened"


class Registry:
    """Tracks which subdomains are currently handed out and their tunnels."""

    def __init__(self, base_domain: str, default_lifetime_minutes: int = 120) -> None:
        self._base_domain = base_domain
        self._default_lifetime = timedelta(minutes=default_lifetime_minutes)
        self._taken: set[str] = set()
        self._tunnels: dict[str, TunnelInfo] = {}

    @property
    def base_domain(self) -> str:
        return self._base_domain

    @property
    def active_domains(self) -> set[str]:
        """Return a snapshot of currently assigned subdomains."""
        return set(self._taken)

    def __len__(self) -> int:
        return len(self._taken)

    @property
    def active_tunnel_count(self) -> int:
        """Number of tunnels with a bound listener that are not closed/expired."""
        return sum(
            1
            for info in self._tunnels.values()
            if info.listen_port is not None and info.status not in ("closed", "expired")
        )

    def acquire(self) -> tuple[str, str]:
        """Reserve a random free subdomain and return (domain, token).

        Raises SubdomainPoolExhaustedError if no free label is found.
        """
        now = datetime.now(UTC)
        for _ in range(_MAX_ATTEMPTS):
            label = _generate_label()
            domain = f"{label}.{self._base_domain}"
            if domain not in self._taken:
                self._taken.add(domain)
                token = _generate_token()
                self._tunnels[domain] = TunnelInfo(
                    token=token,
                    created_at=now,
                    expires_at=now + self._default_lifetime,
                    status="opened",
                )
                return domain, token
        raise SubdomainPoolExhaustedError(
            f"no free subdomain left under {self._base_domain}"
        )

    def get_info(self, domain: str) -> TunnelInfo | None:
        """Return tunnel info for an assigned subdomain, if any."""
        return self._tunnels.get(domain)

    def get_token(self, domain: str) -> str | None:
        """Return the WebLogs token for an assigned subdomain, if any."""
        info = self._tunnels.get(domain)
        return info.token if info else None

    def bind_listener(self, domain: str, listen_port: int) -> None:
        """Bind a server-side listener port to a subdomain."""
        if domain not in self._taken:
            raise KeyError(f"domain {domain} is not assigned")
        if domain in self._tunnels and self._tunnels[domain].listen_port is not None:
            raise RuntimeError(f"domain {domain} already has a tunnel")
        info = self._tunnels[domain]
        self._tunnels[domain] = TunnelInfo(
            listen_port=listen_port,
            token=info.token,
            created_at=info.created_at,
            expires_at=info.expires_at,
            status=info.status,
        )

    def get_listener_port(self, domain: str) -> int | None:
        """Return the server-side listener port for a subdomain, if any."""
        info = self._tunnels.get(domain)
        return info.listen_port if info else None

    def extend(
        self,
        domain: str,
        minutes: int,
        *,
        max_extend_minutes: int | None = None,
        max_total_lifetime_minutes: int | None = None,
    ) -> datetime:
        """Extend the lifetime of a tunnel by the given minutes.

        Args:
            domain: the assigned subdomain to extend.
            minutes: minutes to add to the current expiry.
            max_extend_minutes: if set, reject a single request larger than this.
            max_total_lifetime_minutes: if set, reject a request that would push
                the tunnel's total lifetime (measured from ``created_at``) past
                this many minutes.

        Raises:
            KeyError: if the domain is not assigned.
            ExtendLimitError: if the request exceeds ``max_extend_minutes``,
                would exceed ``max_total_lifetime_minutes``, or overflows
                datetime arithmetic.
        """
        if domain not in self._taken:
            raise KeyError(f"domain {domain} is not assigned")
        if max_extend_minutes is not None and minutes > max_extend_minutes:
            raise ExtendLimitError(
                f"cannot extend by more than {max_extend_minutes} minutes at once"
            )
        info = self._tunnels[domain]
        try:
            new_expires = max(info.expires_at, datetime.now(UTC)) + timedelta(
                minutes=minutes
            )
            max_expires = (
                info.created_at + timedelta(minutes=max_total_lifetime_minutes)
                if max_total_lifetime_minutes is not None
                else None
            )
        except OverflowError as exc:
            raise ExtendLimitError("extend value is too large") from exc
        if max_expires is not None and new_expires > max_expires:
            raise ExtendLimitError(
                "cannot extend beyond the maximum total lifetime of "
                f"{max_total_lifetime_minutes} minutes"
            )
        self._tunnels[domain] = TunnelInfo(
            listen_port=info.listen_port,
            token=info.token,
            created_at=info.created_at,
            expires_at=new_expires,
            status="extended",
        )
        return new_expires

    def set_status(self, domain: str, status: _TunnelStatus) -> None:
        """Update the status of an assigned tunnel."""
        if domain not in self._taken:
            raise KeyError(f"domain {domain} is not assigned")
        info = self._tunnels[domain]
        self._tunnels[domain] = TunnelInfo(
            listen_port=info.listen_port,
            token=info.token,
            created_at=info.created_at,
            expires_at=info.expires_at,
            status=status,
        )

    def get_expired(self) -> list[str]:
        """Return a list of assigned subdomains whose lifetime has expired."""
        now = datetime.now(UTC)
        return [
            domain for domain, info in self._tunnels.items() if info.expires_at <= now
        ]

    def release(self, domain: str) -> None:
        """Return a subdomain to the pool. Releasing an unknown domain is a no-op."""
        self._taken.discard(domain)
        self._tunnels.pop(domain, None)
