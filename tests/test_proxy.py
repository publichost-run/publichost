import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from publichost.tunnel.proxy import (
    HttpProxy,
    _framing_is_ambiguous,
    _parse_host,
    _redact_headers,
)
from publichost.tunnel.registry import Registry


@pytest.mark.parametrize(
    "headers,expected",
    [
        (b"GET / HTTP/1.1\r\nHost: foo.example.com\r\n\r\n", "foo.example.com"),
        (b"GET / HTTP/1.1\r\nHOST: FOO.EXAMPLE.COM\r\n\r\n", "foo.example.com"),
        (b"GET / HTTP/1.1\r\nHost: foo.example.com:8080\r\n\r\n", "foo.example.com"),
        (b"GET / HTTP/1.1\r\n\r\n", None),
        (b"\xff\xff", None),
    ],
)
def test_parse_host(headers, expected):
    assert _parse_host(headers) == expected


# --- Fix 3: sensitive header redaction --------------------------------------


def test_redact_headers_masks_sensitive_values():
    headers = {
        "Host": "foo.example.com",
        "Authorization": "Bearer secret-token",
        "Cookie": "session=abc123",
        "Proxy-Authorization": "Basic Zm9vOmJhcg==",
        "Set-Cookie": "session=abc123; HttpOnly",
        "User-Agent": "curl/8.0",
    }
    redacted = _redact_headers(headers)

    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["Cookie"] == "[REDACTED]"
    assert redacted["Proxy-Authorization"] == "[REDACTED]"
    assert redacted["Set-Cookie"] == "[REDACTED]"
    # Header names stay visible; non-sensitive values are untouched.
    assert set(redacted) == set(headers)
    assert redacted["Host"] == "foo.example.com"
    assert redacted["User-Agent"] == "curl/8.0"
    # Original dict is not mutated.
    assert headers["Authorization"] == "Bearer secret-token"


# --- Test scaffolding for the proxy integration paths -----------------------


@dataclass
class _Capture:
    events: list[dict]


class _RecordingPublisher:
    """Stand-in for EventPublisher that records published log payloads."""

    def __init__(self, capture: _Capture) -> None:
        self._capture = capture

    async def publish_log(self, **kwargs) -> None:
        self._capture.events.append(kwargs)


@asynccontextmanager
async def _running_proxy(
    registry: Registry,
    *,
    publisher: object | None = None,
    max_header_bytes: int = 16384,
) -> AsyncIterator[int]:
    proxy = HttpProxy(
        registry,
        "127.0.0.1",
        0,
        publisher,  # type: ignore[arg-type]
        max_header_bytes=max_header_bytes,
    )
    server = await proxy.start()
    task = asyncio.create_task(server.serve_forever())
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        server.close()
        await server.wait_closed()


@asynccontextmanager
async def _backend(
    registry: Registry,
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], object],
) -> AsyncIterator[str]:
    """Start a backend server, register it as a tunnel, and yield its domain."""
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    domain, _token = registry.acquire()
    registry.bind_listener(domain, port)
    try:
        yield domain
    finally:
        server.close()
        await server.wait_closed()


# --- Fix 1: header flood must not crash the handler or leak the fd -----------


async def test_header_flood_does_not_crash_and_closes_connection():
    """A never-terminated, oversized header block must be rejected cleanly.

    Before the fix, readuntil raised LimitOverrunError which escaped
    _handle_client, so writer.close() never ran (fd leak). Now the connection
    is answered/closed deterministically once max_header_bytes is exceeded.
    """
    registry = Registry("publichost.test")

    async with _running_proxy(registry, max_header_bytes=4096) as proxy_port:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        # Valid request line, then a giant header value with no terminator.
        writer.write(b"GET / HTTP/1.1\r\nX-Flood: ")
        writer.write(b"A" * 70000)
        await writer.drain()

        # The proxy must respond and/or close the socket rather than hang or
        # crash. read() returns b"" once the proxy closes its side.
        data = await asyncio.wait_for(reader.read(4096), timeout=5)
        assert data == b"" or data.startswith(b"HTTP/1.1 431")
        writer.close()
        await writer.wait_closed()


# --- Fix 2: per-request logging on a keep-alive connection ------------------


async def test_keepalive_logs_each_request_with_status_and_latency():
    registry = Registry("publichost.test")
    capture = _Capture(events=[])
    publisher = _RecordingPublisher(capture)

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Serve exactly one request per (per-request) backend connection.
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 201 Created\r\nContent-Length: 3\r\n\r\nabc")
        await writer.drain()
        writer.close()

    async with _backend(registry, handler) as domain:
        async with _running_proxy(registry, publisher=publisher) as proxy_port:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)

            # Two requests over one keep-alive client connection.
            writer.write(f"GET /first HTTP/1.1\r\nHost: {domain}\r\n\r\n".encode())
            await writer.drain()
            resp1 = await asyncio.wait_for(
                reader.readexactly(
                    len(b"HTTP/1.1 201 Created\r\nContent-Length: 3\r\n\r\nabc")
                ),
                timeout=5,
            )
            assert b"201 Created" in resp1

            writer.write(
                f"POST /second HTTP/1.1\r\nHost: {domain}\r\n"
                f"Content-Length: 2\r\n\r\nhi".encode()
            )
            await writer.drain()
            resp2 = await asyncio.wait_for(
                reader.readexactly(
                    len(b"HTTP/1.1 201 Created\r\nContent-Length: 3\r\n\r\nabc")
                ),
                timeout=5,
            )
            assert b"201 Created" in resp2

            writer.close()
            await writer.wait_closed()

    # One event per request, each with the parsed status code (not None).
    assert len(capture.events) == 2
    paths = [e["path"] for e in capture.events]
    assert paths == ["/first", "/second"]
    assert all(e["status_code"] == 201 for e in capture.events)
    assert [e["method"] for e in capture.events] == ["GET", "POST"]
    # Per-request latency, not whole-connection lifetime.
    assert all(e["latency_ms"] >= 0 for e in capture.events)


async def test_published_headers_are_redacted():
    registry = Registry("publichost.test")
    capture = _Capture(events=[])
    publisher = _RecordingPublisher(capture)

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
        writer.close()

    async with _backend(registry, handler) as domain:
        async with _running_proxy(registry, publisher=publisher) as proxy_port:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(
                f"GET / HTTP/1.1\r\nHost: {domain}\r\n"
                f"Authorization: Bearer supersecret\r\n"
                f"Cookie: sid=deadbeef\r\n\r\n".encode()
            )
            await writer.drain()
            await asyncio.wait_for(reader.read(4096), timeout=5)
            writer.close()
            await writer.wait_closed()

    assert len(capture.events) == 1
    headers = capture.events[0]["headers"]
    assert headers["Authorization"] == "[REDACTED]"
    assert headers["Cookie"] == "[REDACTED]"
    # No secret value leaked anywhere in the serialized headers.
    assert "supersecret" not in str(headers)
    assert "deadbeef" not in str(headers)


# --- Request smuggling: ambiguous Content-Length/Transfer-Encoding ----------


@pytest.mark.parametrize(
    "header_block",
    [
        # CL + TE together: RFC 9112 6.1 says this must be treated as an error.
        b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n",
        # Two disagreeing Content-Length values (classic CL.CL smuggling).
        b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\nContent-Length: 6\r\n\r\n",
    ],
)
def test_framing_is_ambiguous_detects_smuggling_vectors(header_block):
    assert _framing_is_ambiguous(header_block) is True


def test_framing_is_ambiguous_allows_normal_requests():
    assert (
        _framing_is_ambiguous(
            b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\n\r\n"
        )
        is False
    )
    assert (
        _framing_is_ambiguous(
            b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
        )
        is False
    )
    assert _framing_is_ambiguous(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n") is False
    # Duplicate Content-Length lines with the *same* value are unambiguous.
    assert (
        _framing_is_ambiguous(
            b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\n"
            b"Content-Length: 4\r\n\r\n"
        )
        is False
    )


async def test_smuggling_request_is_rejected_before_reaching_backend():
    """A request with conflicting CL/TE framing must be rejected with 400.

    Before the fix, the proxy forwarded Content-Length and Transfer-Encoding
    verbatim while framing the body by its own chunked-first rule, letting a
    single client request desync into two backend requests -- the second one
    invisible to WebLogs. The backend below would raise if it ever saw a
    second request line, proving the smuggled request never arrives.
    """
    registry = Registry("publichost.test")
    backend_hits = []

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        backend_hits.append(await reader.read(65536))
        writer.close()

    async with _backend(registry, handler) as domain:
        async with _running_proxy(registry) as proxy_port:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(
                f"POST /visible HTTP/1.1\r\nHost: {domain}\r\n"
                f"Content-Length: 44\r\nTransfer-Encoding: chunked\r\n\r\n"
                f"0\r\n\r\nGET /smuggled HTTP/1.1\r\nHost: {domain}\r\n\r\n".encode()
            )
            await writer.drain()
            data = await asyncio.wait_for(reader.read(4096), timeout=5)
            writer.close()
            await writer.wait_closed()

    assert data.startswith(b"HTTP/1.1 400")
    assert backend_hits == []
