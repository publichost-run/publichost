"""Tests for BannerSession's shell-command handling, in particular Ctrl+C."""

from __future__ import annotations

import asyncssh

from publichost.tunnel.config import Settings
from publichost.tunnel.registry import Registry
from publichost.tunnel.session import BannerSession


class _FakeChannel:
    """Minimal stand-in for asyncssh.SSHServerChannel."""

    def __init__(self) -> None:
        self.written: list[str] = []
        self.closed = False

    def write(self, data: str) -> None:
        self.written.append(data)

    def close(self) -> None:
        self.closed = True


def _make_session() -> tuple[BannerSession, _FakeChannel]:
    registry = Registry("publichost.test")
    domain, token = registry.acquire()
    settings = Settings()
    session = BannerSession(domain, token, registry, settings)
    chan = _FakeChannel()
    session.connection_made(chan)  # type: ignore[arg-type]
    return session, chan


def test_ctrl_c_closes_the_channel():
    """A bare Ctrl+C (ETX/0x03) must close the tunnel, per the banner's promise."""
    session, chan = _make_session()

    session.data_received("\x03", None)

    assert chan.closed is True
    assert any("^C" in chunk for chunk in chan.written)
    assert any("Goodbye" in chunk for chunk in chan.written)


def test_ctrl_c_discards_partial_line():
    """Ctrl+C mid-line cancels the pending input instead of running it as a command."""
    session, chan = _make_session()

    session.data_received("ext", None)
    session.data_received("\x03", None)

    assert chan.closed is True
    # The partial "ext" must never be interpreted as a command once ^C lands.
    assert not any("Unknown command" in chunk for chunk in chan.written)


def test_ctrl_c_in_stderr_datatype_is_ignored():
    """Only real shell input (not stderr) should be interpreted as a command."""
    session, chan = _make_session()

    session.data_received("\x03", asyncssh.EXTENDED_DATA_STDERR)

    assert chan.closed is False


def test_exit_still_closes_the_channel():
    """Existing `exit` command behavior must be unchanged."""
    session, chan = _make_session()

    session.data_received("exit\r\n", None)

    assert chan.closed is True
    assert any("Goodbye" in chunk for chunk in chan.written)


def test_banner_points_weblogs_at_the_base_domain():
    """WebLogs are served by the API on the bare base domain, not a frontend."""
    from datetime import UTC, datetime

    from publichost.tunnel.session import _format_banner

    banner = _format_banner(
        "swift-fox-1.example.com",
        "tok123",
        datetime.now(UTC),
        "example.com",
        "example.com",
    )
    assert "https://swift-fox-1.example.com" in banner
    assert "https://example.com/?token=tok123" in banner
