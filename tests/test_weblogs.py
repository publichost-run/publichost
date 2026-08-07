"""Tests for the WebLogs WebSocket handler.

These exercise the handler directly with in-memory fakes instead of a live
WebSocket, because `httpx`/Starlette's TestClient is not installed and the real
uvicorn behaviour (close-before-accept -> HTTP 403) is exactly what we are
guarding against.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from publichost.api.routers import weblogs


class FakeWebSocket:
    """Records the ordered sequence of WS operations performed on it."""

    def __init__(self, incoming: list[str] | None = None) -> None:
        self.events: list[tuple] = []
        self.accepted = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._incoming = list(incoming or [])

    async def accept(self) -> None:
        self.events.append(("accept",))
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.events.append(("close", code, reason))
        self.close_code = code
        self.close_reason = reason

    async def send_json(self, data: dict) -> None:
        self.events.append(("send_json", data))

    async def receive_text(self) -> str:
        if self._incoming:
            return self._incoming.pop(0)
        # Block forever to emulate a connected-but-idle client.
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FakePubSub:
    def __init__(self, messages: list[dict] | None = None) -> None:
        self._messages = list(messages or [])
        self.subscribed: list[str] = []

    async def subscribe(self, *channels: str) -> None:
        self.subscribed.extend(channels)

    async def listen(self):
        for message in self._messages:
            yield message


class FakeRedisClient:
    def __init__(self, pubsub: FakePubSub) -> None:
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self) -> FakePubSub:
        return self._pubsub

    async def close(self) -> None:
        self.closed = True


def _index(events: list[tuple], name: str) -> int:
    for i, event in enumerate(events):
        if event[0] == name:
            return i
    raise AssertionError(f"event {name!r} not found in {events!r}")


# --------------------------------------------------------------------------- #
# Fix 1: accept() must precede close() so custom WS close codes reach the client
# --------------------------------------------------------------------------- #


async def test_tunnel_not_found_accepts_then_closes_with_code(monkeypatch):
    async def fake_get_tunnel(token: str):
        return None

    monkeypatch.setattr(weblogs, "_get_tunnel_by_token", fake_get_tunnel)
    ws = FakeWebSocket()

    await weblogs.weblogs_websocket(ws, token="missing")

    # Regression: current code closed *before* accept, so accept never happened.
    assert ws.accepted, "handler must accept before closing so code reaches client"
    assert _index(ws.events, "accept") < _index(ws.events, "close")
    assert ws.close_code == 4004
    assert ws.close_reason == "TUNNEL_NOT_FOUND"


@pytest.mark.parametrize(
    ("status", "reason"),
    [("closed", "TUNNEL_CLOSED"), ("expired", "TUNNEL_EXPIRED")],
)
async def test_tunnel_closed_or_expired_accepts_then_closes(
    monkeypatch, status, reason
):
    async def fake_get_tunnel(token: str):
        return SimpleNamespace(id=1, subdomain="foo", status=status, token=token)

    monkeypatch.setattr(weblogs, "_get_tunnel_by_token", fake_get_tunnel)
    ws = FakeWebSocket()

    await weblogs.weblogs_websocket(ws, token="tok")

    assert ws.accepted
    assert _index(ws.events, "accept") < _index(ws.events, "close")
    assert ws.close_code == 4004
    assert ws.close_reason == reason


# --------------------------------------------------------------------------- #
# Fix 2: CSV writes happen off the event loop and only once per token
# --------------------------------------------------------------------------- #


def _log_message(token: str) -> dict:
    return {
        "type": "message",
        "data": json.dumps(
            {
                "token": token,
                "event": "log.request",
                "method": "GET",
                "path": "/",
                "headers": {},
            }
        ),
    }


async def test_persist_writes_via_to_thread(monkeypatch):
    async def fake_get_tunnel(token: str):
        return SimpleNamespace(id=7, subdomain="foo", status="opened", token=token)

    monkeypatch.setattr(weblogs, "_get_tunnel_by_token", fake_get_tunnel)

    to_thread_calls: list = []

    async def fake_to_thread(fn, *args, **kwargs):
        to_thread_calls.append(fn)
        return None

    monkeypatch.setattr(weblogs.asyncio, "to_thread", fake_to_thread)

    ws = FakeWebSocket()
    pubsub = FakePubSub([_log_message("tok")])

    await weblogs._consume_logs(ws, "tok", pubsub, persist=True)

    # The blocking file write must be dispatched off the event loop.
    assert weblogs._append_log in to_thread_calls
    # And the log is still forwarded to the client.
    assert any(e[0] == "send_json" for e in ws.events)


async def test_non_writer_does_not_persist(monkeypatch):
    calls: list = []

    async def fake_to_thread(fn, *args, **kwargs):
        calls.append(fn)
        return None

    monkeypatch.setattr(weblogs.asyncio, "to_thread", fake_to_thread)

    ws = FakeWebSocket()
    pubsub = FakePubSub([_log_message("tok")])

    await weblogs._consume_logs(ws, "tok", pubsub, persist=False)

    # De-dupe: a second concurrent handler for the same token must not write.
    assert weblogs._append_log not in calls
    # It still forwards the log to its own client.
    assert any(e[0] == "send_json" for e in ws.events)


async def test_claim_csv_writer_is_exclusive_per_token():
    weblogs._csv_writer_tokens.discard("tok")
    first = await weblogs._claim_csv_writer("tok")
    second = await weblogs._claim_csv_writer("tok")
    assert first is True
    assert second is False
    await weblogs._release_csv_writer("tok")
    third = await weblogs._claim_csv_writer("tok")
    assert third is True
    await weblogs._release_csv_writer("tok")


# --------------------------------------------------------------------------- #
# Fix 3: a failing consumer task tears the handler down instead of leaking
# --------------------------------------------------------------------------- #


async def test_consumer_failure_does_not_hang_handler(monkeypatch):
    async def fake_get_tunnel(token: str):
        return SimpleNamespace(id=1, subdomain="foo", status="opened", token=token)

    monkeypatch.setattr(weblogs, "_get_tunnel_by_token", fake_get_tunnel)
    monkeypatch.setattr(weblogs.settings, "redis_url", "redis://localhost:6379/0")

    pubsub = FakePubSub()
    client = FakeRedisClient(pubsub)

    async def fake_from_url(*args, **kwargs):
        return client

    monkeypatch.setattr(weblogs.redis, "from_url", fake_from_url)

    # Consumer blows up immediately; receive loop would otherwise wait forever.
    async def boom(*args, **kwargs):
        raise RuntimeError("consumer exploded")

    monkeypatch.setattr(weblogs, "_consume_logs", boom)

    ws = FakeWebSocket()  # receive_text() blocks forever

    # Before the fix this hangs on the while-True receive loop; after the fix the
    # handler notices the consumer died and tears everything down.
    await asyncio.wait_for(weblogs.weblogs_websocket(ws, token="tok"), timeout=2.0)

    # The per-connection Redis client must be closed on the way out.
    assert client.closed
    # No writer ownership must be left dangling.
    assert "tok" not in weblogs._csv_writer_tokens
