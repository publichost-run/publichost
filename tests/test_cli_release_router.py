"""Tests for the CLI version endpoint."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import Response

from publichost.api.routers import cli_release


async def test_cli_version_reflects_settings(monkeypatch) -> None:
    fake_settings = SimpleNamespace(
        cli_latest_version="2.3.4",
        cli_min_supported_version="2.0.0",
        cli_released_at="2026-08-09",
        cli_release_url="https://example.com/releases/v2.3.4",
        cli_install_command="curl -fsSL https://example.com/install.sh | sh",
    )
    monkeypatch.setattr(cli_release, "settings", fake_settings)

    response = Response()
    result = await cli_release.cli_version(response)

    assert result.latest == "2.3.4"
    assert result.min_supported == "2.0.0"
    assert result.released_at == "2026-08-09"
    assert result.release_url == "https://example.com/releases/v2.3.4"
    assert result.install_command == "curl -fsSL https://example.com/install.sh | sh"


async def test_cli_version_sets_cache_control_header() -> None:
    response = Response()
    await cli_release.cli_version(response)
    assert response.headers["Cache-Control"] == "public, max-age=300"
