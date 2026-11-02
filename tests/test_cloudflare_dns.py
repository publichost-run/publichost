"""Tests for deploy/cloudflare_dns.py (Cloudflare DNS upsert used by deploy.sh)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[1] / "deploy" / "cloudflare_dns.py"
_spec = importlib.util.spec_from_file_location("cloudflare_dns", _PATH)
assert _spec is not None and _spec.loader is not None
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)


class _FakeApi:
    def __init__(self, zones: dict[str, str], records: list[dict[str, Any]]):
        self.zones = zones
        self.records = records
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, token, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("/zones?"):
            name = path.split("name=", 1)[1]
            zone_id = self.zones.get(name)
            return [{"id": zone_id, "name": name}] if zone_id else []
        if method == "GET":
            name = path.split("name=", 1)[1].replace("%2A", "*")
            return [r for r in self.records if r["name"] == name]
        return {}


def test_find_zone_walks_up_to_the_registered_zone(monkeypatch):
    api = _FakeApi({"example.com": "z1"}, [])
    monkeypatch.setattr(cf, "_request", api)
    assert cf.find_zone("t", "tun.example.com") == ("z1", "example.com")


def test_find_zone_fails_without_zone(monkeypatch):
    monkeypatch.setattr(cf, "_request", _FakeApi({}, []))
    with pytest.raises(cf.CloudflareError):
        cf.find_zone("t", "tun.example.com")


def test_upsert_creates_missing_record(monkeypatch):
    api = _FakeApi({}, [])
    monkeypatch.setattr(cf, "_request", api)
    assert cf.upsert_a_record("t", "z1", "*.example.com", "1.2.3.4") == "created"
    method, path, body = api.calls[-1]
    assert method == "POST"
    assert body == {
        "type": "A",
        "name": "*.example.com",
        "content": "1.2.3.4",
        "ttl": 1,
        "proxied": False,
    }


def test_upsert_keeps_matching_record(monkeypatch):
    record = {"id": "r1", "type": "A", "name": "example.com", "content": "1.2.3.4"}
    api = _FakeApi({}, [{**record, "proxied": False}])
    monkeypatch.setattr(cf, "_request", api)
    assert cf.upsert_a_record("t", "z1", "example.com", "1.2.3.4") == "unchanged"
    assert [c[0] for c in api.calls] == ["GET"]


def test_upsert_unproxies_and_repoints_record(monkeypatch):
    record = {"id": "r1", "type": "A", "name": "example.com", "content": "9.9.9.9"}
    api = _FakeApi({}, [{**record, "proxied": True}])
    monkeypatch.setattr(cf, "_request", api)
    assert cf.upsert_a_record("t", "z1", "example.com", "1.2.3.4") == "updated"
    method, path, body = api.calls[-1]
    assert (method, path) == ("PUT", "/zones/z1/dns_records/r1")
    assert body is not None and body["proxied"] is False


def test_upsert_refuses_cname(monkeypatch):
    record = {"id": "r1", "type": "CNAME", "name": "example.com", "content": "x"}
    monkeypatch.setattr(cf, "_request", _FakeApi({}, [record]))
    with pytest.raises(cf.CloudflareError):
        cf.upsert_a_record("t", "z1", "example.com", "1.2.3.4")
