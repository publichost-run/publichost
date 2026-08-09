"""Tests for the public API pydantic schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from publichost.api.schemas import CliVersionResponse, StatsResponse


def test_cli_version_response_parses() -> None:
    model = CliVersionResponse(
        latest="1.0.0",
        min_supported="1.0.0",
        released_at="2026-08-09",
        release_url="https://github.com/publichost-run/publichost-cli/releases/tag/v1.0.0",
        install_command="curl -fsSL https://raw.githubusercontent.com/publichost-run/publichost-cli/main/install.sh | sh",  # noqa: E501
    )
    assert model.latest == "1.0.0"


def test_stats_response_parses() -> None:
    model = StatsResponse(
        active_tunnels=1289,
        requests_24h=485003,
        relay_status="operational",
        updated_at="2026-08-09T12:00:00Z",
    )
    assert model.relay_status == "operational"


def test_stats_response_rejects_bogus_relay_status() -> None:
    with pytest.raises(ValidationError):
        StatsResponse(
            active_tunnels=1,
            requests_24h=1,
            relay_status="bogus",
            updated_at="2026-08-09T12:00:00Z",
        )
