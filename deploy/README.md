# Deploy examples

Topology: **nginx on the host, outside Docker**. Two application services run
inside Docker (`docker-compose.yml`): `publichost` (tunnel service — SSH +
HTTP reverse proxy) and `api` (FastAPI + Celery worker/beat). Redis and
PostgreSQL are internal to the Compose network. nginx never touches SSH
traffic — port 2222 (or 22, see `AGENTS.md`) is published directly from the
`publichost` container.

```
                 ┌───────────────────────────────┐
clients ── SSH ──▶ publichost container :2222     │
                 │  (SSH server + HTTP proxy)     │
                 └───────────────────────────────┘
                             ▲
                             │ 127.0.0.1:8080
nginx (host, :443) ─────────┘   *.publichost.run  →  deploy/nginx.publichost.conf.example
                 │
                 └── 127.0.0.1:8000  publichost.io →  deploy/nginx.api.conf.example
                                     (FastAPI + /channel/weblogs WS)
```

## Files here

- `nginx.publichost.conf.example` — host nginx → tunnel service (`:8080`),
  wildcard `*.<base_domain>` routing for tunnel HTTP/WebSocket traffic.
- `nginx.api.conf.example` — host nginx → API service (`:8000`), including
  the `/channel/weblogs` WebSocket (auth via `?token=`). `proxy_read_timeout`
  must be at least as long as the max tunnel lifetime, or nginx will drop
  the socket before the tunnel itself expires.
- `systemd/publichost-compose.service.example` — starts/stops the Compose
  stack on boot.

## Deploy steps

```bash
cp .env.docker.example .env.docker   # fill in domains, ports, secrets
docker compose up -d --build
docker compose logs -f
```

Then, on the host (outside Docker):

```bash
sudo cp deploy/nginx.publichost.conf.example /etc/nginx/sites-available/publichost.conf
sudo cp deploy/nginx.api.conf.example /etc/nginx/sites-available/publichost-api.conf
sudo ln -s /etc/nginx/sites-available/publichost.conf /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/publichost-api.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Wildcard TLS for `*.<base_domain>` requires a DNS-01 challenge (e.g.
`certbot --dns-01`) since HTTP-01 cannot validate a wildcard.

Port 22: either move the host `sshd` to another port and publish
`"22:2222"` in `docker-compose.yml`, or bind a dedicated IP for the
`publichost` container. Do not change without confirming with the user
(see `AGENTS.md`).
