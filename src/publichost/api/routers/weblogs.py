"""WebLogs WebSocket endpoint."""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import redis.asyncio as redis
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..config import settings
from ..db import AsyncSessionLocal
from ..models import Tunnel

logger = logging.getLogger(__name__)
router = APIRouter()

# Guards CSV persistence so that concurrent WS handlers for the same token do
# not each write every log line (which would duplicate rows). Only the first
# handler to claim a token persists; the rest are read-only presentation.
_csv_writer_lock = asyncio.Lock()
_csv_writer_tokens: set[str] = set()


async def _claim_csv_writer(token: str) -> bool:
    """Claim CSV-write ownership for a token. Returns True if this caller won."""
    async with _csv_writer_lock:
        if token in _csv_writer_tokens:
            return False
        _csv_writer_tokens.add(token)
        return True


async def _release_csv_writer(token: str) -> None:
    """Release CSV-write ownership previously claimed for a token."""
    async with _csv_writer_lock:
        _csv_writer_tokens.discard(token)


def _tunnel_csv_path(subdomain: str, tunnel_id: int) -> Path:
    """Return the CSV file path for a tunnel's WebLogs."""
    date_stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return Path(settings.weblogs_path) / f"{subdomain}-{date_stamp}-{tunnel_id}.csv"


def _ensure_csv(path: Path) -> None:
    """Create the CSV file with headers if it does not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "timestamp",
                    "method",
                    "host",
                    "path",
                    "status_code",
                    "latency_ms",
                    "headers",
                    "body",
                ]
            )


def _append_log(path: Path, payload: dict[str, Any]) -> None:
    """Append a single log row to the tunnel CSV file."""
    _ensure_csv(path)
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                payload.get("timestamp", ""),
                payload.get("method", ""),
                payload.get("host", ""),
                payload.get("path", ""),
                payload.get("status_code", ""),
                payload.get("latency_ms", ""),
                json.dumps(payload.get("headers", {})),
                payload.get("body", ""),
            ]
        )


async def _get_tunnel_by_token(token: str) -> Tunnel | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Tunnel).where(Tunnel.token == token))
        return result.scalar_one_or_none()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read all rows from the CSV file (sync, called via asyncio.to_thread)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


async def _read_existing_logs(path: Path, websocket: WebSocket) -> None:
    """Send existing CSV rows to the WebSocket client."""
    rows = await asyncio.to_thread(_read_csv_rows, path)
    for row in rows:
        await websocket.send_json({"type": "log", "data": row})


async def _consume_logs(
    websocket: WebSocket,
    token: str,
    pubsub: redis.client.PubSub,
    persist: bool,
) -> None:
    """Listen for Redis log events and send matching ones to the client.

    When ``persist`` is True this handler also appends log rows to the tunnel
    CSV (off the event loop via ``asyncio.to_thread``). Only one WS handler per
    token persists to avoid duplicate rows (see ``_claim_csv_writer``).
    """
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            payload = json.loads(message["data"])
        except json.JSONDecodeError:
            continue
        if payload.get("token") != token:
            continue

        event = payload.get("event")
        if event == "log.request":
            if persist:
                tunnel = await _get_tunnel_by_token(token)
                if tunnel is not None:
                    await asyncio.to_thread(
                        _append_log,
                        _tunnel_csv_path(tunnel.subdomain, tunnel.id),
                        payload,
                    )
            await websocket.send_json({"type": "log", "data": payload})
            continue

        if event in ("tunnel.closed", "tunnel.expired"):
            status_code = event.split(".", 1)[1].upper()
            error = f"TUNNEL_{status_code}"
            await websocket.send_json({"type": "error", "data": error})
            await websocket.close(code=4000, reason=error)
            return


def _log_consumer_exception(task: asyncio.Task[None]) -> None:
    """Surface consumer-task failures instead of letting asyncio swallow them."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("WebLogs consumer task failed", exc_info=exc)


async def _receive_until_disconnect(websocket: WebSocket) -> None:
    """Drain client-sent frames until the socket disconnects."""
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return


async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
    """Cancel and await a task, tolerating cancellation/disconnect fallout."""
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, WebSocketDisconnect):
        pass


@router.websocket("/channel/weblogs")
async def weblogs_websocket(
    websocket: WebSocket,
    token: str = Query(...),
) -> None:
    """WebSocket endpoint for real-time WebLogs of a single tunnel."""
    tunnel = await _get_tunnel_by_token(token)
    if tunnel is None:
        # Accept before closing so the custom WS close code reaches the client;
        # closing before accept makes uvicorn reject with a plain HTTP 403.
        await websocket.accept()
        await websocket.close(code=4004, reason="TUNNEL_NOT_FOUND")
        return

    if tunnel.status in ("closed", "expired"):
        await websocket.accept()
        await websocket.close(code=4004, reason=f"TUNNEL_{tunnel.status.upper()}")
        return

    await websocket.accept()
    csv_path = _tunnel_csv_path(tunnel.subdomain, tunnel.id)
    await _read_existing_logs(csv_path, websocket)

    if not settings.redis_url or settings.redis_url.lower() in ("none", ""):
        await websocket.send_json({"type": "status", "data": "Redis is disabled"})
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass
        return

    client = await redis.from_url(settings.redis_url, decode_responses=True)
    is_writer = False
    consumer_task: asyncio.Task[None] | None = None
    receive_task: asyncio.Task[None] | None = None
    try:
        pubsub = client.pubsub()
        await pubsub.subscribe("publichost:logs", "publichost:tunnels")
        is_writer = await _claim_csv_writer(token)
        consumer_task = asyncio.create_task(
            _consume_logs(websocket, token, pubsub, persist=is_writer)
        )
        consumer_task.add_done_callback(_log_consumer_exception)
        receive_task = asyncio.create_task(_receive_until_disconnect(websocket))
        # Tear down as soon as either side finishes: a client disconnect, or the
        # consumer raising/returning. Otherwise a dead producer would leave the
        # receive loop waiting forever and leak the consumer task + Redis conn.
        await asyncio.wait(
            {consumer_task, receive_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        await _cancel_task(consumer_task)
        await _cancel_task(receive_task)
        if is_writer:
            await _release_csv_writer(token)
        await client.close()
