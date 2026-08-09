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
tasks. Not yet implemented: abuse protection and multiple tunnels per
connection.

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
    routers/weblogs.py      # WebSocket /channel/weblogs?token=
    routers/cli_release.py  # public GET /api/v1/cli/version (CLI release metadata)
    routers/stats.py        # public GET /api/v1/stats (cached service stats)
    celery/
      app.py      # Celery app (task name: publichost.cleanup_expired_tunnels)
      tasks.py    # Celery tasks
    cli.py        # CLI entry points for api/worker/beat
tests/          # pytest, src-layout (package is installed editable via uv sync)
Dockerfile      # multi-stage: uv build + slim runtime, non-root user
docker-compose.yml  # publichost, api, worker, beat, redis, postgres
deploy/         # example configs for host nginx + systemd (outside Docker)
```

Environment variables for the SSH/proxy server: `PUBLICHOST_BASE_DOMAIN`,
`PUBLICHOST_SSH_DOMAIN`, `PUBLICHOST_FRONTEND_DOMAIN`, `PUBLICHOST_SSH_HOST`,
`PUBLICHOST_SSH_PORT`, `PUBLICHOST_HOST_KEY_PATH`, `PUBLICHOST_HTTP_HOST`,
`PUBLICHOST_HTTP_PORT`, `PUBLICHOST_DEFAULT_LIFETIME_MINUTES`,
`PUBLICHOST_EXPIRE_CHECK_INTERVAL_SECONDS`, `PUBLICHOST_REDIS_URL`,
`PUBLICHOST_WEBLOGS_PATH`, `PUBLICHOST_HEARTBEAT_INTERVAL_SECONDS` (relay
heartbeat interval, default `10`), `PUBLICHOST_INSTANCE_ID` (relay id in the
heartbeat payload, falls back to hostname) (see docstring in `config.py`).

Environment variables for the API: `PUBLICHOST_DATABASE_URL`,
`PUBLICHOST_REDIS_URL`, `PUBLICHOST_WEBLOGS_PATH`, `PUBLICHOST_API_HOST`,
`PUBLICHOST_API_PORT`, `PUBLICHOST_CLI_LATEST_VERSION` (default `1.0.0`),
`PUBLICHOST_CLI_MIN_SUPPORTED_VERSION` (default `1.0.0`),
`PUBLICHOST_CLI_RELEASED_AT`, `PUBLICHOST_CLI_RELEASE_URL`,
`PUBLICHOST_CLI_INSTALL_COMMAND`, `PUBLICHOST_STATS_CACHE_SECONDS` (in-process
cache TTL for `/api/v1/stats`, default `5`), `PUBLICHOST_CORS_ORIGINS`
(comma-separated allowed origins for the public REST endpoints, default
`https://publichost.io,https://www.publichost.io`) (see
`src/publichost/api/config.py`).

Public API endpoints: `GET /api/v1/cli/version` and `GET /api/v1/stats` are the
only unauthenticated public HTTP endpoints on the API. They serve CLI release
metadata and aggregate service stats respectively, are CORS-restricted to
`PUBLICHOST_CORS_ORIGINS`, and rate-limited by the host nginx (see
`deploy/nginx.api.conf.example`).

The CLI lives in `cli/` and is a **separate Python distribution**
(`publichost-cli`) with its own virtualenv and lock file. It must never be
installed into the backend's root `.venv`: both distributions expose a console
script literally named `publichost` (backend: the SSH server daemon; CLI: the
client), so installing them together collides. Build/test the CLI via its own
`cli/` venv (see the `cli-*` Makefile targets).

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
3. **Abuse protection**: rate limit connections by IP, limit tunnels per
   connection.

## Deployment (Docker)

Topology: **nginx on the host, outside Docker**. Inside the container: SSH server
+ HTTP reverse proxy. nginx does not proxy SSH traffic — port 22/2222 is
published directly from the container. HTTP/HTTPS traffic flow: nginx (443) →
`127.0.0.1:8080` → container:8080.

- `Dockerfile` — multi-stage: uv installs dependencies from `uv.lock`, runtime
  `python:3.12-slim`, non-root user `app`, entrypoint `python -m publichost`.
- `docker-compose.yml` — `publichost` service, named volume `hostkey` → `/data`
  (host key survives rebuilds; deleting the volume rotates the key), SSH and
  HTTP-proxy ports. External ports and domains are read from `.env.docker`
  (template: `.env.docker.example`).
- Commands:
  ```bash
  cp .env.docker.example .env.docker
  docker compose up -d --build
  docker compose logs -f
  ```
- Example nginx server blocks and a systemd unit for the host are in
  `deploy/` (`nginx.publichost.conf.example`, `nginx.api.conf.example`,
  `systemd/publichost-compose.service.example`), see `deploy/README.md`.
- Production port 22: either `22:2222` after moving the host sshd, or a
  dedicated IP — do not change without user confirmation.

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

In `.kimi-code/skills/`: `run-dev-server` (local start and connect),
`test-tunnel` (E2E tunnel check), `ship-check` (pre-commit checks),
`deploy-docker` (build and deploy via Docker).
