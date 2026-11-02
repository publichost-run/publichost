# PublicHost

Self-hosted SSH tunnels with one-time subdomains, in the spirit of
localhost.run / serveo. Anyone can expose a local web server with plain `ssh`:
no signup, no client to install.

```bash
ssh -p 2222 -R 0:localhost:3000 tunnel.example.com
```

The server answers with a banner:

```
┌──────────────────────────────────────────────────────────────┐
│  PublicHost — your one-time tunnel is ready                  │
│  HTTPS URL:   https://swift-fox-123.tunnel.example.com       │
│  WebLogs:     https://tunnel.example.com/?token=…            │
│  Expires:     2026-11-02 14:00:00 UTC                        │
│  ...                                                         │
└──────────────────────────────────────────────────────────────┘
```

- **One-time subdomain** per SSH connection, released when it disconnects.
- **Automatic HTTPS** via a Let's Encrypt wildcard certificate.
- **WebLogs**: live request inspector in the browser (method, path, status,
  latency, headers, body; `Authorization`/`Cookie` redacted).
- **Lifetime limit** (default 2 h), extendable with `extend <minutes>`.
- **Abuse protection**: per-IP and global connection limits, per-IP tunnel cap.

## Self-host in one command

You need:

1. A Linux server with a public IPv4 address (1 vCPU / 1 GB RAM is enough),
   with ports **80**, **443** and the SSH port for clients (default **2222**)
   free and open.
2. A domain on **Cloudflare** DNS (a subdomain like `tunnel.example.com` works
   too) and a Cloudflare API token from the **Edit zone DNS** template.

Then, on the server:

```bash
git clone https://github.com/publichost-run/publichost-backend.git
```

```bash
cd publichost-backend && ./deploy.sh
```

`deploy.sh` asks for the domain, an email for Let's Encrypt, the SSH port and
the Cloudflare token, and then:

- installs Docker if it is missing (after asking);
- writes `.env.docker` with a generated database password;
- creates the DNS records `<domain>` and `*.<domain>` in Cloudflare (DNS only);
- opens the ports in `ufw`/`firewalld` if one of them is active;
- builds and starts the stack, waits for the wildcard certificate and checks
  that routing works;
- prints the `ssh` command for your users and the server's host key fingerprint.

Non-interactive (e.g. from a provisioning script):

```bash
CF_API_TOKEN=... ./deploy.sh --domain tunnel.example.com --email you@example.com --ssh-port 2222 -y
```

Day-2 operations:

| Command | What it does |
|---|---|
| `./deploy.sh update` | `git pull`, rebuild, restart (configuration is kept) |
| `./deploy.sh status` | container status |
| `./deploy.sh logs [service]` | follow logs |
| `./deploy.sh restart` / `down` | restart / stop (data is kept) |
| `./deploy.sh --reconfigure` | change domain, email, port or token |
| `./deploy.sh uninstall` | stop and **delete** all data |

Port **22** for clients is possible only after moving the server's own `sshd`
to another port (make sure you can still log in first). See
[deploy/README.md](deploy/README.md) for the architecture, where secrets live
and how to test against the Let's Encrypt staging CA.

## Using a tunnel

```bash
python3 -m http.server 3000                         # something on localhost:3000
ssh -p 2222 -R 0:localhost:3000 tunnel.example.com  # keep this session open
```

Inside the session type `extend 30` to add 30 minutes, `help` for commands, and
Ctrl+C to close the tunnel. `https://<domain>/` shows the command to run.

## Configuration

`deploy.sh` manages the essentials. Everything else is in `.env.docker` (see
the comments in [.env.docker.example](.env.docker.example)); re-run
`./deploy.sh` after editing it.

| Variable | Description | Default |
|---|---|---|
| `PUBLICHOST_BASE_DOMAIN` | Tunnels are `<label>.<base_domain>` | set by deploy.sh |
| `PUBLICHOST_SSH_DOMAIN` | Host clients SSH into | = base domain |
| `PUBLICHOST_EXTERNAL_SSH_PORT` | Public SSH port | `2222` |
| `PUBLICHOST_ACME_EMAIL` | Let's Encrypt account email | set by deploy.sh |
| `PUBLICHOST_ACME_CA_SERVER` | ACME directory (production or staging) | Let's Encrypt production |
| `PUBLICHOST_DEFAULT_LIFETIME_MINUTES` | Default tunnel lifetime | `120` |
| `PUBLICHOST_MAX_EXTEND_MINUTES` | Max minutes one `extend` adds | `120` |
| `PUBLICHOST_MAX_TOTAL_LIFETIME_MINUTES` | Max total tunnel lifetime | `1440` |
| `PUBLICHOST_MAX_TUNNELS_PER_IP` | Concurrent tunnels per client IP (min 1) | `2` |
| `PUBLICHOST_MAX_CONNECTIONS_PER_IP` | Concurrent SSH connections per IP | `10` |
| `PUBLICHOST_MAX_TOTAL_CONNECTIONS` | Concurrent SSH connections in total | `500` |
| `PUBLICHOST_CORS_ORIGINS` | Extra browser origins for `/api/v1/*` | empty |
| `PUBLICHOST_TRAEFIK_IMAGE` | Traefik image | `traefik:v3.7` |

Advanced/internal variables (bind addresses, internal ports, Redis/DB URLs,
heartbeat, CLI release metadata) are documented in
[`src/publichost/tunnel/config.py`](src/publichost/tunnel/config.py) and
[`src/publichost/api/config.py`](src/publichost/api/config.py).

## Public HTTP API

Served on `https://<domain>/`:

- `GET /` — landing page; with `?token=<token>` the WebLogs viewer.
- `WS /channel/weblogs?token=<token>` — live request log stream.
- `GET /api/v1/stats` — active tunnels, requests in the last 24 h, relay status.
- `GET /api/v1/cli/version` — release metadata for an optional client CLI.

## Development

```bash
uv sync                        # install dependencies (requires uv)
cp .env.example .env           # local settings (Redis disabled)
uv run python -m publichost    # SSH server :2222 + HTTP proxy :8080
ssh -p 2222 -R 0:localhost:3000 localhost
curl -H "Host: <label>.publichost.run" http://localhost:8080/
```

```bash
uv run pytest -q      # tests
uv run ruff check     # lint
uv run ruff format    # formatting
```

Architecture, conventions and roadmap: [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
