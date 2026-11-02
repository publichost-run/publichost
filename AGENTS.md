# PublicHost-Backend — Agent Instructions

## What is this project

An SSH service for one-time subdomains (similar to localhost.run / serveo). A
client connects to our server over SSH, and the server hands out an address
`<label>.<base_domain>` that proxies traffic to the client's localhost. The
subdomain is one-time: it lives exactly as long as the SSH connection and is
released when the connection drops.

**Current status: product-ready features implemented.** Working now:
passwordless login, one-time subdomain assignment per connection, banner
session with HTTPS URL and WebLogs link, `ssh -R 0:localhost:<port>`, HTTP
reverse proxy by `Host` header, automatic tunnel lifetime (default 2 hours),
`extend <minutes>` command, Redis-based event publishing, FastAPI backend with
PostgreSQL persistence, WebLogs via WebSocket + CSV files, and Celery cleanup
tasks. Abuse protection is implemented: per-IP and server-wide connection
limits, plus a per-IP concurrent-tunnel cap (`PUBLICHOST_MAX_TUNNELS_PER_IP`).
The project is open source (MIT) and self-hostable with a single
`./deploy.sh` (Docker Compose + Traefik + Cloudflare DNS-01 wildcard TLS).
There is no separate frontend: the API serves a built-in landing page and the
WebLogs viewer at `https://<base_domain>/?token=<token>`.
Not yet implemented: multiple tunnels per single connection.

## Stack

- Python ≥ 3.11, `asyncssh` (the whole server is asyncio, no blocking calls)
- Package manager: **uv** (`uv sync`, lock file is committed)
- Tests: pytest + pytest-asyncio (`asyncio_mode = "auto"`)
- Lint/format: ruff (rules `E, F, I, UP, B, ASYNC`)

## Commands

```bash
uv sync                        # install dependencies (first time and after pyproject changes)
uv run python -m publichost    # start server (port 2222, see env below)
uv run pytest -q               # tests
uv run ruff check              # lint
uv run ruff format             # formatting
```

Before finishing any task, `ruff check` and `pytest` must be green.

## Structure

```
src/publichost/
  __main__.py     # entry point: python -m publichost (delegates to tunnel.app)
  common/         # shared by both services
    config.py     # CommonSettings: PUBLICHOST_REDIS_URL, PUBLICHOST_WEBLOGS_PATH
    events.py     # Redis event publisher (EventPublisher)
    stats.py      # shared aggregation for the public /api/v1/stats endpoint
  tunnel/         # service 1: SSH server + HTTP reverse proxy
    __main__.py   # entry point: python -m publichost.tunnel
    app.py        # composition root: starts SSH server, proxy, expiry monitor
    config.py     # Settings(CommonSettings): PUBLICHOST_SSH_*, HTTP_*, lifetime
    hostkey.py    # ensure_host_key: generate/load the SSH host key
    heartbeat.py  # periodic relay-liveness heartbeat written to Redis
    registry.py   # Registry: subdomain assignment/release + tunnel port/lifetime
    server.py     # PublicHostSSHServer (asyncssh): auth, remote forwarding only
    session.py    # BannerSession: banner + `extend`/`help`/`exit` commands
    proxy.py      # HTTP reverse proxy: Host → tunnel listener port + WebLogs
  api/            # service 2: FastAPI backend + Celery + WebLogs WebSocket
    main.py       # FastAPI app
    config.py     # ApiSettings (pydantic-settings)
    schemas.py    # Pydantic models for the /channel/weblogs WS contract
    models.py     # SQLAlchemy models
    db.py         # async engine/session
    consumer.py   # Redis pub/sub consumer, persists tunnel events to Postgres
    routers/pages.py        # GET / — landing page + WebLogs viewer (?token=)
    routers/weblogs.py      # WebSocket /channel/weblogs?token=
    routers/cli_release.py  # public GET /api/v1/cli/version (CLI release metadata)
    routers/stats.py        # public GET /api/v1/stats (cached service stats)
    celery/
      app.py      # Celery app (task name: publichost.cleanup_expired_tunnels)
      tasks.py    # Celery tasks
    cli.py        # CLI entry points for api/worker/beat
tests/          # pytest, src-layout (package is installed editable via uv sync)
Dockerfile      # multi-stage: uv build + slim runtime, non-root user
docker-compose.yml  # traefik, publichost, api, worker, beat, redis, postgres
deploy.sh       # one-command self-host installer / updater (see deploy/README.md)
deploy/         # Traefik dynamic config template + Cloudflare DNS helper
.github/        # CI (tests+coverage on 3.11-3.13, shellcheck, docker build), Dependabot, templates
```

Environment variables for the SSH/proxy server: `PUBLICHOST_BASE_DOMAIN`,
`PUBLICHOST_SSH_DOMAIN`, `PUBLICHOST_SSH_HOST`,
`PUBLICHOST_SSH_PORT`, `PUBLICHOST_HOST_KEY_PATH`, `PUBLICHOST_HTTP_HOST`,
`PUBLICHOST_HTTP_PORT`, `PUBLICHOST_DEFAULT_LIFETIME_MINUTES`,
`PUBLICHOST_EXPIRE_CHECK_INTERVAL_SECONDS`, `PUBLICHOST_REDIS_URL`,
`PUBLICHOST_WEBLOGS_PATH`, `PUBLICHOST_MAX_TUNNELS_PER_IP` (max concurrent
remote-forward tunnels per client IP, clamped to a minimum of `1`, default
`2`), `PUBLICHOST_HEARTBEAT_INTERVAL_SECONDS` (relay heartbeat interval,
default `10`), `PUBLICHOST_INSTANCE_ID` (relay id in the heartbeat payload,
falls back to hostname) (see docstring in `config.py`).

Environment variables for the API: `PUBLICHOST_DATABASE_URL`,
`PUBLICHOST_REDIS_URL`, `PUBLICHOST_WEBLOGS_PATH`, `PUBLICHOST_API_HOST`,
`PUBLICHOST_API_PORT`, `PUBLICHOST_CLI_LATEST_VERSION` (default `1.0.0`),
`PUBLICHOST_CLI_MIN_SUPPORTED_VERSION` (default `1.0.0`),
`PUBLICHOST_CLI_RELEASED_AT`, `PUBLICHOST_CLI_RELEASE_URL`,
`PUBLICHOST_CLI_INSTALL_COMMAND`, `PUBLICHOST_STATS_CACHE_SECONDS` (in-process
cache TTL for `/api/v1/stats`, default `5`), `PUBLICHOST_CORS_ORIGINS`
(comma-separated extra origins for the public REST endpoints, default empty:
the built-in page is same-origin), `PUBLICHOST_SSH_DOMAIN` and
`PUBLICHOST_EXTERNAL_SSH_PORT` (shown in the landing page's `ssh` command) (see
`src/publichost/api/config.py`).

Deployment-only variables (read by `deploy.sh` / `docker-compose.yml`):
`PUBLICHOST_EXTERNAL_SSH_PORT`, `PUBLICHOST_ACME_EMAIL`,
`PUBLICHOST_ACME_CA_SERVER`, `PUBLICHOST_POSTGRES_USER`,
`PUBLICHOST_POSTGRES_PASSWORD`, `PUBLICHOST_POSTGRES_DB`,
`PUBLICHOST_TRAEFIK_IMAGE`. The Cloudflare API token lives in
`secrets/cloudflare_dns_api_token` (gitignored), mounted only into Traefik.

Public API endpoints (all on `https://<base_domain>`): `GET /` (landing page /
WebLogs viewer, strict hash-based CSP, `Referrer-Policy: no-referrer` because
the token is in the URL), `GET /api/v1/cli/version` and `GET /api/v1/stats`.
The REST endpoints are CORS-restricted to `PUBLICHOST_CORS_ORIGINS` and
rate-limited by Traefik (`api-ratelimit` middleware in
`deploy/traefik/dynamic.yml.tmpl`).

The optional client CLI (`publichost-cli`) lives in a separate repository
(`cli/` is gitignored). It must never be installed into the backend's `.venv`:
both distributions expose a console script literally named `publichost`.

Environment files:
- `.env.example` — template for local development; copy to `.env`.
- `.env.docker.example` — template for Docker Compose; copy to `.env.docker`.
- `.env` and `.env.docker` are in `.gitignore` and must not be committed.

## Conventions

- All repository artifacts — code, identifiers, comments, docstrings, and
  documentation (README.md, AGENTS.md, skills) — are written in English.
- The agent replies to the user in the language the user is writing in.
- async/await only; blocking code goes through `asyncio.to_thread` with a
  justification.
- Type hints everywhere; `from __future__ import annotations` in every module.
- Minimal changes: do not refactor or "improve" code outside the task.
- Git mutations (commit/push/reset) only on explicit user request.

## Security (important, this is a public service)

- `host_key` is generated on first run, lives in `/data` (volume), and is **in
  .gitignore**. Never commit keys, `.env`, or secrets.
- Do not give clients shell or exec: session is banner only (`_BannerSession`).
  Remote port forwarding (`server_requested`) is allowed and is the core feature.
  Any other request handlers (sftp, direct-tcpip, etc.) are forbidden by default
  unless the task explicitly requires otherwise.
- Subdomain labels are generated as `adjective-noun-number` (e.g.
  `swift-fox-123`) via `secrets` for readability and sufficient entropy.
- Do not log tunnel traffic data.

## MVP Roadmap (what we build next)

1. ~~**Remote forwarding**: `SSHServer.server_requested` in `server.py` accepts
   `ssh -R 0:localhost:<port>`, creates a listener, and binds it to the assigned
   subdomain in `Registry`.~~ ✅ Implemented.
2. ~~**HTTP reverse proxy**: listen on 8080, route by `Host: <label>.publichost.run`
   header to the correct tunnel.~~ ✅ Implemented.
3. **Abuse protection**: per-IP and server-wide connection limits and a per-IP
   concurrent-tunnel cap (`PUBLICHOST_MAX_TUNNELS_PER_IP`) are ✅ implemented;
   supporting multiple tunnels per single connection is still pending.

## Deployment (Docker)

Single supported mode: a Linux server with a domain on Cloudflare, set up by
`./deploy.sh` (see README "Self-host in one command" and `deploy/README.md`).
Topology: **Traefik inside Compose** terminates TLS (wildcard Let's Encrypt
cert via Cloudflare DNS-01) on 80/443 and routes `<label>.<domain>` →
`publichost:8080` and `<domain>` → `api:8000`. SSH is published directly from
the `publichost` container; the HTTP proxy, API, Redis and Postgres are not
published on the host.

- `Dockerfile` — multi-stage: uv installs dependencies from `uv.lock`, runtime
  `python:3.12-slim`, non-root user `app`, entrypoint `python -m publichost`.
- `docker-compose.yml` — named volume `hostkey` → `/data` (host key survives
  rebuilds; deleting the volume rotates the key), `letsencrypt` for certs.
  Values are interpolated from `.env.docker` (template: `.env.docker.example`),
  so always run compose via `./deploy.sh` or with `--env-file .env.docker`.
  The compose project name is the directory name — do not add `name:` (it
  would orphan existing volumes).
- Commands:
  ```bash
  ./deploy.sh            # install / reconfigure
  ./deploy.sh update     # git pull + rebuild
  ./deploy.sh logs       # follow logs
  ```
- Client SSH port 22 only after the host sshd is moved elsewhere — `deploy.sh`
  refuses to bind a busy port; do not change this without user confirmation.

## Agent hierarchy (`.claude/agents/`)

Non-trivial tasks go through the hierarchy (orchestrated by the `/feature`
skill, or manually):

0. `architect` (Claude Opus 5) — for large features and structural decisions:
   evaluates architecture fit, gives 2–3 alternatives with trade-offs, picks
   one with justification; no code.
1. `planner` (Claude Opus 5) — designs the step-by-step plan; no code.
2. `coder` (Claude Opus 4.8) — implements the plan, runs pytest + ruff.
3. `reviewer` (Claude Opus 5) — reviews the diff: security, async safety,
   correctness. Verdict APPROVE / NEEDS FIXES; findings go back to `coder`.
4. `refactorer` (Claude Opus 4.8) — behavior-preserving cleanup after APPROVE.

Rules for every agent: reply in the user's language (Russian in → Russian out,
English in → English out); code and identifiers always in English; be concrete —
files, lines, commands, no generic advice.

## Useful skills

In `.claude/skills/`: `feature` (full planner → coder → reviewer → refactorer
pipeline), `security-audit` (defensive audit of the tunneling service),
`api-contract` (API document for the Frontend team), `git-flow` (branch/commit/
PR conventions), `doc-generate` (generate docs/ incl. frontend guide).
