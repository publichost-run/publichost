# Deployment

Everything here is driven by [`../deploy.sh`](../deploy.sh). You normally never
touch these files directly.

## Topology

```
                         ┌──────────────── docker compose ────────────────┐
clients ── SSH :2222 ────┼─▶ publichost   (SSH server + HTTP proxy :8080)  │
                         │        ▲                                       │
browsers ─ HTTPS :443 ───┼─▶ traefik ── <label>.<domain> ──┘              │
           (HTTP :80 →   │        └──── <domain> ─────────▶ api :8000     │
            redirect)    │                                  worker, beat  │
                         │                                  redis, postgres│
                         └────────────────────────────────────────────────┘
```

- **traefik** terminates TLS with a single Let's Encrypt wildcard certificate
  (`<domain>` + `*.<domain>`) obtained through the Cloudflare DNS-01 challenge.
  It routes `<label>.<domain>` to the tunnel proxy and the bare `<domain>` to
  the API (landing page, WebLogs viewer at `/?token=…`, `/api/v1/*`, with a
  rate limit on `/api/v1/`).
- **publichost** publishes only the SSH port. Its HTTP proxy, the API, Redis
  and PostgreSQL are reachable only on the internal compose network.

## Files

- `traefik/dynamic.yml.tmpl` — Traefik routers/services. `deploy.sh` renders it
  to `traefik/dynamic.yml` (gitignored) with your domain.
- `cloudflare_dns.py` — creates/updates the two DNS-only A records
  (`<domain>`, `*.<domain>`) via the Cloudflare API. Stdlib only.

## Secrets and state

| What | Where |
|---|---|
| Settings, generated Postgres password | `.env.docker` (mode 600, gitignored) |
| Cloudflare API token | `secrets/cloudflare_dns_api_token` (mode 600, gitignored), mounted only into Traefik as a compose secret |
| SSH host key | volume `hostkey` — deleting it rotates the key (clients see a host-key-changed warning) |
| TLS certificates | volume `letsencrypt` |
| Database, WebLogs, Redis | volumes `pgdata`, `weblogs`, `redisdata` |

## Cloudflare token

dash.cloudflare.com → My Profile → API Tokens → Create Token → template
**Edit zone DNS** → Zone Resources: *Include / Specific zone / your zone*.
The same token is used by `deploy.sh` (DNS records) and by Traefik (certificate
issuance and renewal). Records are created as **DNS only** (grey cloud):
Cloudflare's proxy cannot carry SSH.

## Testing

`./deploy.sh --staging` uses the Let's Encrypt staging CA (untrusted
certificates, no rate limits). Switch back with
`./deploy.sh --reconfigure`, then delete the `letsencrypt` volume so the
staging certificate is replaced:

```bash
./deploy.sh down
docker volume rm "$(basename "$PWD" | tr -cd 'a-z0-9_-')_letsencrypt"
./deploy.sh
```
