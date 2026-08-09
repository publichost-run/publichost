"""Pydantic schemas for the public API contracts.

Covers the `/channel/weblogs` WebSocket contract plus the two public REST
endpoints (`/api/v1/cli/version` and `/api/v1/stats`).

Frontend contract: every message sent over the WebSocket is a JSON object
shaped like one of the models below, discriminated by the `type` field.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class WeblogsLogEntry(BaseModel):
    """A single HTTP request/response entry (also the CSV row shape)."""

    timestamp: str
    method: str
    host: str
    path: str
    status_code: str | int | None = None
    latency_ms: str | float | None = None
    headers: str | dict[str, str] = Field(
        description="JSON-encoded string when replayed from CSV, dict when live"
    )
    body: str = ""


class WeblogsLogMessage(BaseModel):
    """Sent for every request relayed through the tunnel."""

    type: Literal["log"] = "log"
    data: WeblogsLogEntry


class WeblogsStatusMessage(BaseModel):
    """Informational message, e.g. when Redis is disabled."""

    type: Literal["status"] = "status"
    data: str


class WeblogsErrorMessage(BaseModel):
    """Sent right before the server closes the socket.

    `data` is one of: TUNNEL_NOT_FOUND, TUNNEL_CLOSED, TUNNEL_EXPIRED.
    """

    type: Literal["error"] = "error"
    data: str


WeblogsMessage = Annotated[
    WeblogsLogMessage | WeblogsStatusMessage | WeblogsErrorMessage,
    Field(discriminator="type"),
]


class CliVersionResponse(BaseModel):
    """GET /api/v1/cli/version — lets the CLI know if it's out of date."""

    latest: str
    min_supported: str
    released_at: str
    release_url: str
    install_command: str


class StatsResponse(BaseModel):
    """GET /api/v1/stats — public live numbers shown on the marketing site."""

    active_tunnels: int
    requests_24h: int
    relay_status: Literal["operational", "degraded", "down"]
    updated_at: str
