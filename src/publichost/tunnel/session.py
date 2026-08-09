"""Banner SSH session: shows tunnel details and handles the `extend` command.

The service never grants shell or exec access: this session only prints a
banner and interprets a tiny fixed command set (`extend`, `help`,
`exit`/`quit`). Do not add exec/sftp/shell passthrough here.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

import asyncssh

from ..common.events import EventPublisher
from .config import Settings
from .registry import ExtendLimitError, Registry

logger = logging.getLogger(__name__)

_EXTEND_RE = re.compile(r"^extend\s+(\d+)$")


def _format_datetime(dt: datetime) -> str:
    """Format a datetime as a human-readable UTC string."""
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_banner(
    domain: str,
    token: str,
    expires_at: datetime,
    frontend_domain: str,
    ssh_domain: str,
) -> str:
    """Return a framed ASCII banner showing tunnel details."""
    https_url = f"https://{domain}"
    weblogs_url = f"https://{frontend_domain}/channel/weblogs?token={token}"
    expires_line = f"Expires:     {_format_datetime(expires_at)}"
    lines = [
        "",
        "PublicHost — your one-time tunnel is ready",
        f"HTTPS URL:   {https_url}",
        f"WebLogs:     {weblogs_url}",
        expires_line,
        "SSH host:    " + ssh_domain,
        "",
        "Keep this SSH session open to keep the tunnel alive.",
        "Type 'extend <minutes>' to add time (e.g. extend 30).",
        "Press Ctrl+C or close the session to stop.",
        "",
    ]
    content_width = max(len(line) for line in lines)
    border = "─" * (content_width + 4)

    def _row(text: str) -> str:
        return f"│  {text:<{content_width}}  │"

    framed = ["", f"┌{border}┐"]
    for line in lines[1:-1]:
        framed.append(_row(line))
    framed.extend([f"└{border}┘", ""])
    return "\r\n".join(framed)


class BannerSession(asyncssh.SSHServerSession):
    """Session that prints the banner and handles simple shell commands."""

    def __init__(
        self,
        domain: str,
        token: str,
        registry: Registry,
        settings: Settings,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._domain = domain
        self._token = token
        self._registry = registry
        self._settings = settings
        self._publisher = publisher
        self._chan: asyncssh.SSHServerChannel | None = None
        self._input_buffer: str = ""

    def connection_made(self, chan: asyncssh.SSHServerChannel) -> None:
        self._chan = chan

    def shell_requested(self) -> bool:
        if self._chan is None:
            return False
        info = self._registry.get_info(self._domain)
        expires_at = info.expires_at if info else datetime.now(UTC)
        self._chan.write(
            _format_banner(
                self._domain,
                self._token,
                expires_at,
                self._settings.frontend_domain,
                self._settings.ssh_domain,
            )
        )
        return True

    def data_received(self, data: str, datatype: asyncssh.DataType) -> None:
        """Handle user input typed into the shell."""
        # asyncssh sends regular shell input with datatype None and stderr as
        # EXTENDED_DATA_STDERR. We only care about user-typed shell input.
        if datatype == asyncssh.EXTENDED_DATA_STDERR:
            return
        if self._chan is None:
            return
        # This session has no real PTY line discipline, so a Ctrl+C keypress
        # arrives as a literal ETX (0x03) byte rather than a SIGINT the way
        # it would against a real remote shell. The banner promises "Press
        # Ctrl+C to stop", so treat it the same as typing `exit`, discarding
        # whatever partial line preceded it (matching normal shell behavior
        # of ^C cancelling the current input line).
        if "\x03" in data:
            self._chan.write("^C\r\n")
            self._input_buffer = ""
            self._handle_command("exit")
            return
        # Echo input back to the client.
        self._chan.write(data)
        self._input_buffer += data
        while True:
            newline_idx = self._input_buffer.find("\n")
            cr_idx = self._input_buffer.find("\r")
            candidates = [i for i in (newline_idx, cr_idx) if i != -1]
            if not candidates:
                break
            idx = min(candidates)
            line = self._input_buffer[:idx].strip()
            self._input_buffer = self._input_buffer[idx + 1 :]
            if line:
                self._handle_command(line)

    def _handle_command(self, line: str) -> None:
        if self._chan is None:
            return
        if not line:
            return

        if line.lower() in ("exit", "quit"):
            self._chan.write("Goodbye.\r\n")
            self._chan.close()
            return

        if line.lower() == "help":
            self._chan.write(
                "Commands:\r\n"
                "  extend <minutes>  extend tunnel lifetime\r\n"
                "  help              show this message\r\n"
                "  exit / quit       close the session\r\n"
            )
            return

        match = _EXTEND_RE.match(line)
        if match:
            minutes = int(match.group(1))
            try:
                new_expires = self._registry.extend(
                    self._domain,
                    minutes,
                    max_extend_minutes=self._settings.max_extend_minutes,
                    max_total_lifetime_minutes=(
                        self._settings.max_total_lifetime_minutes
                    ),
                )
                self._chan.write(
                    f"Extended. New expiry: {_format_datetime(new_expires)}\r\n"
                )
                if self._publisher is not None:
                    asyncio.create_task(
                        self._publisher.publish_tunnel(
                            "tunnel.extended",
                            self._domain,
                            token=self._token,
                            expires_at=new_expires.isoformat(),
                        )
                    )
            except KeyError:
                self._chan.write("Error: tunnel no longer active.\r\n")
            except ExtendLimitError as exc:
                self._chan.write(f"Error: {exc}.\r\n")
            return

        self._chan.write(f"Unknown command: {line}. Type 'help'.\r\n")

    def write_goodbye(self, reason: str = "closed") -> None:
        """Print a short closing message if the channel is still writable."""
        if self._chan is None:
            return
        try:
            self._chan.write(
                f"\r\nTunnel {reason}. https://{self._domain} has been released.\r\n"
            )
        except OSError:
            pass
