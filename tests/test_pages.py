"""Tests for the built-in landing / WebLogs page served on the base domain."""

from __future__ import annotations

import httpx
import pytest

from publichost.api.config import settings
from publichost.api.main import app
from publichost.api.routers import pages


@pytest.fixture
def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_index_serves_html_with_security_headers(_client) -> None:
    async with _client as client:
        resp = await client.get("/", params={"token": "abc"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'sha256-" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["Cache-Control"] == "no-store"
    assert "/channel/weblogs?token=" in resp.text


async def test_index_does_not_reflect_query_into_html(_client) -> None:
    payload = "<script>alert(1)</script>"
    async with _client as client:
        resp = await client.get("/", params={"token": payload})

    assert payload not in resp.text


def test_csp_hashes_match_inline_blocks() -> None:
    page = pages.render_page()
    assert f"<script>{pages._SCRIPT}</script>" in page
    assert f"<style>{pages._STYLE}</style>" in page
    assert pages._csp_hash(pages._SCRIPT) in pages._CSP
    assert pages._csp_hash(pages._STYLE) in pages._CSP


@pytest.mark.parametrize(
    ("port", "expected"),
    [
        (22, "ssh -R 0:localhost:3000 tunnels.example.com"),
        (2222, "ssh -p 2222 -R 0:localhost:3000 tunnels.example.com"),
    ],
)
def test_ssh_command(monkeypatch, port: int, expected: str) -> None:
    monkeypatch.setattr(settings, "ssh_domain", "tunnels.example.com")
    monkeypatch.setattr(settings, "external_ssh_port", port)
    assert pages.ssh_command() == expected


def test_ssh_command_is_html_escaped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ssh_domain", "<b>x</b>")
    assert "&lt;b&gt;x&lt;/b&gt;" in pages.render_page()
