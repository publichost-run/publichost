"""Create/update the Cloudflare DNS records a PublicHost instance needs.

Called by deploy.sh (stdlib only, so it runs on the host's python3 or inside
the backend image). Upserts two DNS-only (grey cloud) A records pointing at
the server:

    <domain>      A  <ip>   (landing page, WebLogs, SSH endpoint)
    *.<domain>    A  <ip>   (tunnel subdomains)

Records must not be proxied (orange cloud): Cloudflare's proxy does not carry
SSH, and TLS is terminated by Traefik with a Let's Encrypt wildcard cert.

The API token is read from the CF_API_TOKEN environment variable and needs the
"Zone / DNS / Edit" permission for the zone (Cloudflare's "Edit zone DNS"
template). Exit code 0 on success, 1 on any error (message on stderr).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    pass


def _request(
    token: str, method: str, path: str, body: dict[str, Any] | None = None
) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.load(exc)
        except ValueError:
            raise CloudflareError(f"{method} {path}: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise CloudflareError(f"cannot reach Cloudflare API: {exc.reason}") from None
    if not payload.get("success"):
        errors = "; ".join(
            f"{e.get('code')}: {e.get('message')}" for e in payload.get("errors", [])
        )
        raise CloudflareError(f"{method} {path}: {errors or 'unknown error'}")
    return payload["result"]


def find_zone(token: str, domain: str) -> tuple[str, str]:
    """Return (zone_id, zone_name) of the most specific zone containing domain."""
    labels = domain.split(".")
    for i in range(len(labels) - 1):
        candidate = ".".join(labels[i:])
        query = urllib.parse.urlencode({"name": candidate})
        zones = _request(token, "GET", f"/zones?{query}")
        if zones:
            return zones[0]["id"], zones[0]["name"]
    raise CloudflareError(
        f"no Cloudflare zone found for {domain}: add the domain to Cloudflare "
        "and give the token access to it"
    )


def upsert_a_record(token: str, zone_id: str, name: str, ip: str) -> str:
    """Make `name` a DNS-only A record pointing at ip. Returns what was done."""
    query = urllib.parse.urlencode({"name": name})
    existing = _request(token, "GET", f"/zones/{zone_id}/dns_records?{query}")
    for record in existing:
        if record["type"] == "CNAME":
            raise CloudflareError(
                f"{name} is a CNAME record; delete it in Cloudflare and re-run"
            )
        if record["type"] == "AAAA":
            print(
                f"warning: {name} also has an AAAA record ({record['content']}); "
                "make sure it points at this server or delete it",
                file=sys.stderr,
            )

    body = {"type": "A", "name": name, "content": ip, "ttl": 1, "proxied": False}
    a_records = [r for r in existing if r["type"] == "A"]
    if not a_records:
        _request(token, "POST", f"/zones/{zone_id}/dns_records", body)
        return "created"
    record, *extra = a_records
    for duplicate in extra:
        _request(token, "DELETE", f"/zones/{zone_id}/dns_records/{duplicate['id']}")
    if record["content"] == ip and not record.get("proxied") and not extra:
        return "unchanged"
    _request(token, "PUT", f"/zones/{zone_id}/dns_records/{record['id']}", body)
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", required=True)
    parser.add_argument("--ip", required=True)
    args = parser.parse_args()

    token = os.environ.get("CF_API_TOKEN", "").strip()
    if not token:
        print("error: CF_API_TOKEN is not set", file=sys.stderr)
        return 1
    try:
        ip = str(ipaddress.IPv4Address(args.ip))
    except ValueError:
        print(f"error: {args.ip!r} is not an IPv4 address", file=sys.stderr)
        return 1
    domain = args.domain.strip().lower().rstrip(".")

    try:
        zone_id, zone_name = find_zone(token, domain)
        print(f"Cloudflare zone: {zone_name}")
        for name in (domain, f"*.{domain}"):
            result = upsert_a_record(token, zone_id, name, ip)
            print(f"  A {name} -> {ip} (DNS only): {result}")
    except CloudflareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
