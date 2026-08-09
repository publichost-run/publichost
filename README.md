# PublicHost-Backend

SSH service for one-time subdomains: a client connects over SSH and receives an
address `<label>.<base_domain>` that proxies HTTP traffic to their localhost.
Similar to localhost.run / serveo. The subdomain lives as long as the SSH
connection is open, and is automatically disconnected after the configured
lifetime (default 2 hours) for security.

## Quick start

```bash
uv sync                        # install dependencies (requires uv)
cp .env.example .env           # local settings
uv run python -m publichost    # SSH + HTTP proxy
ssh -p 2222 -R 0:localhost:3000 localhost  # get a one-time address
```

## Using a tunnel

```bash
# On the client side, start a local service
python3 -m http.server 3000

# Connect with remote forwarding (omit -N so you see the banner)
ssh -R 0:localhost:3000 -p 2222 localhost
# Server prints an ASCII banner with:
#   HTTPS URL:   https://<label>.<base_domain>
#   WebLogs:     https://<frontend_domain>/channel/weblogs?token=<token>
#   Expires:     <ISO time>
# Type 'extend <minutes>' to extend the tunnel lifetime.

# Now http://<label>.<base_domain>/ is proxied to the client's localhost:3000.
```

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `PUBLICHOST_BASE_DOMAIN` | Base domain for tunnel subdomains | `publichost.run` |
| `PUBLICHOST_SSH_DOMAIN` | Domain clients SSH into | `publichost.run` |
| `PUBLICHOST_FRONTEND_DOMAIN` | Domain serving the public web frontend/WebLogs | `publichost.io` |
| `PUBLICHOST_SSH_HOST` | SSH server bind address | `0.0.0.0` |
| `PUBLICHOST_SSH_PORT` | SSH server port | `2222` |
| `PUBLICHOST_HTTP_HOST` | HTTP reverse proxy bind address | `0.0.0.0` |
| `PUBLICHOST_HTTP_PORT` | HTTP reverse proxy port | `8080` |
| `PUBLICHOST_HOST_KEY_PATH` | Path to the SSH host key | `host_key` |
| `PUBLICHOST_DEFAULT_LIFETIME_MINUTES` | Default tunnel lifetime | `120` |
| `PUBLICHOST_EXPIRE_CHECK_INTERVAL_SECONDS` | Expiry scan interval | `60` |
| `PUBLICHOST_REDIS_URL` | Redis URL (`none` to disable locally) | `redis://localhost:6379/0` |
| `PUBLICHOST_WEBLOGS_PATH` | Directory for WebLogs CSV files | `weblogs` |
| `PUBLICHOST_DATABASE_URL` | PostgreSQL URL for the API | `postgresql+asyncpg://...` |
| `PUBLICHOST_API_HOST` | FastAPI bind address | `0.0.0.0` |
| `PUBLICHOST_API_PORT` | FastAPI port | `8000` |
| `PUBLICHOST_HEARTBEAT_INTERVAL_SECONDS` | Relay-liveness heartbeat interval | `10` |
| `PUBLICHOST_INSTANCE_ID` | Relay id in the heartbeat payload | hostname |
| `PUBLICHOST_CLI_LATEST_VERSION` | Latest published CLI version | `1.0.0` |
| `PUBLICHOST_CLI_MIN_SUPPORTED_VERSION` | Oldest supported CLI version | `1.0.0` |
| `PUBLICHOST_CLI_RELEASED_AT` | Latest CLI release date | `` |
| `PUBLICHOST_CLI_RELEASE_URL` | Latest CLI release page URL | `` |
| `PUBLICHOST_CLI_INSTALL_COMMAND` | CLI install command shown to clients | `` |
| `PUBLICHOST_STATS_CACHE_SECONDS` | Cache TTL for `/api/v1/stats` | `5` |
| `PUBLICHOST_CORS_ORIGINS` | Allowed CORS origins for public endpoints | `https://publichost.io,https://www.publichost.io` |

## Public API

Two unauthenticated public endpoints are served by the API: `GET
/api/v1/cli/version` (CLI release metadata) and `GET /api/v1/stats` (aggregate
service stats). The client CLI lives in `cli/` as a separate `publichost-cli`
distribution with its own venv — never install it into the backend's `.venv`.

## Development

```bash
uv run pytest -q      # tests
uv run ruff check     # lint
uv run ruff format    # formatting
```

## Deployment (Docker)

nginx runs on the host outside Docker. Inside the stack: PublicHost SSH server,
HTTP reverse proxy, FastAPI backend, Celery worker/beat, Redis, PostgreSQL.
SSH traffic goes straight to the container; HTTP/HTTPS goes through nginx.

```bash
cp .env.docker.example .env.docker   # deployment settings
docker compose up -d --build
docker compose logs -f
```

`host_key` is stored in the named volume `hostkey` and survives rebuilds.
WebLogs CSV files are stored in the `weblogs` volume.
Example host nginx configs and a systemd unit are in
[deploy/](deploy/README.md).

Architecture, conventions, and roadmap are in [AGENTS.md](AGENTS.md).
