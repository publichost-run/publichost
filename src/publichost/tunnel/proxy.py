"""HTTP reverse proxy: routes by Host header to the right tunnel listener."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import NamedTuple, NoReturn

from ..common.events import EventPublisher
from .registry import Registry

logger = logging.getLogger(__name__)
_PROXY_BACKEND_HOST = "127.0.0.1"
_HEADER_TIMEOUT = 10.0
_MAX_BODY_LOG_BYTES = 16 * 1024
_DEFAULT_MAX_HEADER_BYTES = 16384

# Header names whose values must never reach the WebLogs event stream / CSV.
_SENSITIVE_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization", "set-cookie"}
)
_REDACTED = "[REDACTED]"


def _first_line(header_block: bytes) -> str | None:
    """Return the decoded first line (request/status line) of a header block."""
    try:
        return header_block.splitlines()[0].decode("latin-1")
    except (IndexError, UnicodeDecodeError):
        return None


def _parse_request_line(header_block: bytes) -> tuple[str, str] | None:
    """Parse the HTTP method and path from the request line."""
    line = _first_line(header_block)
    if line is None:
        return None
    parts = line.split()
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _parse_status(header_block: bytes) -> int | None:
    """Parse the numeric status code from an HTTP response status line."""
    line = _first_line(header_block)
    if line is None:
        return None
    parts = line.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _http_version(header_block: bytes) -> str | None:
    """Return the HTTP version (e.g. "1.1") from a request or status line."""
    line = _first_line(header_block)
    if line is None:
        return None
    for token in line.split():
        if token.startswith("HTTP/"):
            return token[len("HTTP/") :]
    return None


def _parse_host(header_block: bytes) -> str | None:
    """Parse the Host header from an HTTP request block."""
    try:
        text = header_block.decode("latin-1")
    except UnicodeDecodeError:
        return None
    for line in text.splitlines():
        if line.lower().startswith("host:"):
            host = line.split(":", 1)[1].strip()
            # Drop port if present (e.g. "host:8080")
            host = host.split(":")[0]
            return host.lower()
    return None


def _parse_headers(header_block: bytes) -> dict[str, str]:
    """Parse all headers from an HTTP request block."""
    headers: dict[str, str] = {}
    try:
        text = header_block.decode("latin-1")
    except UnicodeDecodeError:
        return headers
    for line in text.splitlines()[1:]:
        if not line:
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with sensitive values replaced by a placeholder."""
    return {
        key: (_REDACTED if key.lower() in _SENSITIVE_HEADERS else value)
        for key, value in headers.items()
    }


def _header_value(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _content_length(headers: dict[str, str]) -> int | None:
    """Return a non-negative Content-Length header value, if present and valid."""
    value = _header_value(headers, "content-length")
    if value is None:
        return None
    try:
        length = int(value.strip())
    except ValueError:
        return None
    return length if length >= 0 else None


def _is_chunked(headers: dict[str, str]) -> bool:
    """Return True if the message uses chunked transfer encoding."""
    value = _header_value(headers, "transfer-encoding")
    return value is not None and "chunked" in value.lower()


def _connection_close(headers: dict[str, str]) -> bool | None:
    """Return True/False if Connection specifies close/keep-alive, else None."""
    value = _header_value(headers, "connection")
    if value is None:
        return None
    lowered = value.lower()
    if "close" in lowered:
        return True
    if "keep-alive" in lowered:
        return False
    return None


def _framing_is_ambiguous(header_block: bytes) -> bool:
    """Detect header combinations that enable HTTP request smuggling.

    Per RFC 9112 6.1/6.3, a message with both Content-Length and
    Transfer-Encoding, or multiple Content-Length values that disagree, has
    ambiguous body framing between the proxy and the backend and must be
    rejected outright rather than guessed at.
    """
    try:
        text = header_block.decode("latin-1")
    except UnicodeDecodeError:
        return True
    content_lengths: set[str] = set()
    has_transfer_encoding = False
    for line in text.splitlines()[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key == "content-length":
            content_lengths.add(value.strip())
        elif key == "transfer-encoding":
            has_transfer_encoding = True
    if has_transfer_encoding and content_lengths:
        return True
    return len(content_lengths) > 1


def _request_has_body(headers: dict[str, str]) -> bool:
    """A request has a body only if it advertises framing for one."""
    return _is_chunked(headers) or _content_length(headers) is not None


def _response_has_body(status_code: int | None, method: str) -> bool:
    """Return True if a response with this status/method may carry a body."""
    if method.upper() == "HEAD":
        return False
    if status_code is None:
        return True
    if status_code < 200 or status_code in (204, 304):
        return False
    return True


def _should_keep_alive(
    request_header: bytes,
    request_headers: dict[str, str],
    response_header: bytes,
    response_headers: dict[str, str],
    response_read_until_close: bool,
) -> bool:
    """Decide whether the client connection can be reused for another request."""
    if response_read_until_close:
        # Response body was framed by connection close; nothing more can follow.
        return False
    if _connection_close(request_headers) is True:
        return False
    if _connection_close(response_headers) is True:
        return False
    for raw, headers in (
        (request_header, request_headers),
        (response_header, response_headers),
    ):
        if _http_version(raw) == "1.0" and _connection_close(headers) is not False:
            # HTTP/1.0 defaults to close unless it explicitly opts into keep-alive.
            return False
    return True


async def _safe_close(writer: asyncio.StreamWriter | None) -> None:
    """Close a writer and wait for it, swallowing teardown errors."""
    if writer is None:
        return
    try:
        writer.close()
        await writer.wait_closed()
    except (OSError, RuntimeError):
        pass


async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    """Copy bytes from src to dst until EOF."""
    while True:
        chunk = await src.read(65536)
        if not chunk:
            break
        dst.write(chunk)
        await dst.drain()
    try:
        dst.write_eof()
    except (OSError, RuntimeError):
        pass


async def _forward_body(
    src: asyncio.StreamReader,
    dst: asyncio.StreamWriter,
    headers: dict[str, str],
    *,
    has_body: bool,
    capture_max: int,
) -> tuple[bytes, bool]:
    """Forward a message body from src to dst using HTTP/1.1 framing rules.

    Returns the captured preview (up to ``capture_max`` bytes) and a flag that
    is True when the body was framed by connection close (read until EOF).
    """
    if not has_body:
        return b"", False

    captured = bytearray()

    def capture(chunk: bytes) -> None:
        if capture_max and len(captured) < capture_max:
            captured.extend(chunk[: capture_max - len(captured)])

    if _is_chunked(headers):
        while True:
            size_line = await src.readuntil(b"\r\n")
            dst.write(size_line)
            token = size_line.split(b";", 1)[0].strip()
            try:
                size = int(token, 16)
            except ValueError:
                break
            if size == 0:
                # Consume any trailers up to and including the final blank line.
                while True:
                    trailer = await src.readuntil(b"\r\n")
                    dst.write(trailer)
                    if trailer == b"\r\n":
                        break
                await dst.drain()
                break
            remaining = size + 2  # chunk data + trailing CRLF
            while remaining > 0:
                chunk = await src.read(min(remaining, 65536))
                if not chunk:
                    return bytes(captured), False
                dst.write(chunk)
                await dst.drain()
                capture(chunk)
                remaining -= len(chunk)
        return bytes(captured), False

    length = _content_length(headers)
    if length is not None:
        remaining = length
        while remaining > 0:
            chunk = await src.read(min(remaining, 65536))
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
            capture(chunk)
            remaining -= len(chunk)
        return bytes(captured), False

    # No explicit framing: the body runs until the peer closes (responses only).
    while True:
        chunk = await src.read(65536)
        if not chunk:
            break
        dst.write(chunk)
        await dst.drain()
        capture(chunk)
    return bytes(captured), True


def _decode_body(body: bytes) -> str:
    """Decode a raw body preview for logging."""
    if not body:
        return ""
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1")


class _Backend(NamedTuple):
    """A backend connection kept open for reuse across keep-alive requests.

    Opening a new backend connection means opening a new SSH forwarded-tcpip
    channel, which round-trips to the client's machine. Reopening one per
    request (instead of reusing it like the client-facing connection) turns
    every asset on a request-heavy dev server (Vite/webpack/Next.js) into a
    full network round trip, which is the dominant cost for those pages.
    """

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    host: str


class HttpProxy:
    """Routes incoming HTTP requests to tunnel listeners by Host header."""

    def __init__(
        self,
        registry: Registry,
        host: str,
        port: int,
        publisher: EventPublisher | None = None,
        max_header_bytes: int = _DEFAULT_MAX_HEADER_BYTES,
    ) -> None:
        self._registry = registry
        self._host = host
        self._port = port
        self._publisher = publisher
        self._max_header_bytes = max_header_bytes
        self._server: asyncio.Server | None = None

    async def start(self) -> asyncio.Server:
        """Start the proxy server and return the asyncio Server object."""
        self._server = await asyncio.start_server(
            self._handle_client,
            self._host,
            self._port,
            limit=self._max_header_bytes,
        )
        logger.info("HTTP proxy listening on %s:%d", self._host, self._port)
        return self._server

    async def run(self) -> NoReturn:
        """Start the proxy and run it forever."""
        server = await self.start()
        async with server:
            await server.serve_forever()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        backend: _Backend | None = None
        try:
            while True:
                keep_alive, backend = await self._handle_request(
                    reader, writer, peer, backend
                )
                if not keep_alive:
                    break
        finally:
            if backend is not None:
                await _safe_close(backend.writer)
            await _safe_close(writer)

    async def _handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer: object,
        backend: _Backend | None,
    ) -> tuple[bool, _Backend | None]:
        """Handle a single request/response pair.

        Returns (keep_alive, backend): whether the client connection (and its
        reusable backend connection, if any) should stay open for another
        request.
        """
        started_at = time.perf_counter()

        try:
            header_data = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=_HEADER_TIMEOUT
            )
        except TimeoutError:
            logger.debug("Timeout reading headers from %s", peer)
            return False, backend
        except asyncio.IncompleteReadError:
            # Clean EOF or a partial header block: the client is gone.
            return False, backend
        except asyncio.LimitOverrunError:
            # Header flood: more than max_header_bytes buffered with no terminator.
            logger.debug("Header block over limit from %s", peer)
            writer.write(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
            await writer.drain()
            return False, backend

        host = _parse_host(header_data)
        method_path = _parse_request_line(header_data)
        if host is None or method_path is None:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return False, backend

        if _framing_is_ambiguous(header_data):
            logger.debug("Ambiguous request framing from %s", peer)
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return False, backend

        method, path = method_path
        listen_port = self._registry.get_listener_port(host)
        if listen_port is None:
            logger.info("No tunnel for host %s", host)
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return False, backend

        # Capture tunnel metadata early so logging survives a connection drop.
        tunnel_info = self._registry.get_info(host)
        tunnel_token = tunnel_info.token if tunnel_info else None

        # Reuse the existing backend connection when it still targets the same
        # tunnel and hasn't been torn down; each new one costs a full SSH
        # forwarded-tcpip channel open round trip to the client's machine.
        if backend is not None and (
            backend.host != host or backend.writer.is_closing()
        ):
            await _safe_close(backend.writer)
            backend = None

        if backend is None:
            try:
                backend_reader, backend_writer = await asyncio.open_connection(
                    _PROXY_BACKEND_HOST, listen_port, limit=self._max_header_bytes
                )
            except OSError as exc:
                logger.warning(
                    "Cannot connect to tunnel %s:%d: %s", host, listen_port, exc
                )
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await writer.drain()
                return False, None
            backend = _Backend(backend_reader, backend_writer, host)

        try:
            keep_alive = await self._proxy_exchange(
                reader,
                writer,
                backend.reader,
                backend.writer,
                started_at,
                host,
                method,
                path,
                header_data,
                tunnel_token,
            )
        except Exception:
            await _safe_close(backend.writer)
            raise

        if not keep_alive:
            await _safe_close(backend.writer)
            backend = None

        return keep_alive, backend

    async def _proxy_exchange(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        backend_reader: asyncio.StreamReader,
        backend_writer: asyncio.StreamWriter,
        started_at: float,
        host: str,
        method: str,
        path: str,
        header_data: bytes,
        tunnel_token: str | None,
    ) -> bool:
        """Relay one request/response and publish its WebLogs entry."""
        want_log = self._publisher is not None and tunnel_token is not None
        request_headers = _parse_headers(header_data)

        # Forward the request headers and body to the backend.
        backend_writer.write(header_data)
        try:
            body_preview, _ = await _forward_body(
                reader,
                backend_writer,
                request_headers,
                has_body=_request_has_body(request_headers),
                capture_max=_MAX_BODY_LOG_BYTES if want_log else 0,
            )
            await backend_writer.drain()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
            return False

        # Read the response header block from the backend.
        try:
            response_header = await backend_reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return False

        status_code = _parse_status(response_header)
        response_headers = _parse_headers(response_header)
        writer.write(response_header)

        # A protocol switch (e.g. WebSocket) turns the rest into a raw tunnel.
        if status_code == 101:
            await writer.drain()
            if want_log:
                latency_ms = (time.perf_counter() - started_at) * 1000
                await self._publish_log(
                    host,
                    method,
                    path,
                    status_code,
                    latency_ms,
                    header_data,
                    body_preview,
                    tunnel_token,
                )
            await asyncio.gather(
                _pump(reader, backend_writer),
                _pump(backend_reader, writer),
                return_exceptions=True,
            )
            return False

        try:
            _, read_until_close = await _forward_body(
                backend_reader,
                writer,
                response_headers,
                has_body=_response_has_body(status_code, method),
                capture_max=0,
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
            return False

        if want_log:
            latency_ms = (time.perf_counter() - started_at) * 1000
            await self._publish_log(
                host,
                method,
                path,
                status_code,
                latency_ms,
                header_data,
                body_preview,
                tunnel_token,
            )

        return _should_keep_alive(
            header_data,
            request_headers,
            response_header,
            response_headers,
            read_until_close,
        )

    async def _publish_log(
        self,
        host: str,
        method: str,
        path: str,
        status_code: int | None,
        latency_ms: float,
        header_data: bytes,
        body_preview: bytes,
        tunnel_token: str | None,
    ) -> None:
        if self._publisher is None or tunnel_token is None:
            return
        try:
            await self._publisher.publish_log(
                domain=host,
                token=tunnel_token,
                method=method,
                host=host,
                path=path,
                status_code=status_code,
                latency_ms=latency_ms,
                headers=_redact_headers(_parse_headers(header_data)),
                body=_decode_body(body_preview),
            )
        except Exception as exc:
            logger.warning("Failed to publish log for %s: %s", host, exc)
