---
name: run-dev-server
description: Run the PublicHost SSH server locally for development and debug connections
whenToUse: When you need to start the server locally, connect to it over SSH, or debug subdomain assignment
---

# Local PublicHost server

1. Install dependencies: `uv sync`
2. Start the server: `uv run python -m publichost`
   - Default SSH port is 2222, HTTP proxy is 8080.
   - On first run a `host_key` file is created in the project root
     (it is in `.gitignore` — do not commit it).
   - Logs will show: `SSH server listening on 0.0.0.0:2222` and
     `HTTP proxy listening on 0.0.0.0:8080`.
3. Connect as a client: `ssh -p 2222 localhost`
   - No password or key is required (passwordless).
   - The server prints an ASCII banner with the HTTPS URL: `https://<label>.publichost.run`.
4. To test the tunnel, start a local service:
   `python3 -m http.server 3000`, then in another terminal run:
   `ssh -R 0:localhost:3000 -p 2222 localhost`.
   Now `http://<label>.publichost.run/` is proxied to `localhost:3000`.
5. Close the SSH session and check the log for `Released <label>.publichost.run` —
   the subdomain has returned to the pool.

## Settings via env

`PUBLICHOST_BASE_DOMAIN`, `PUBLICHOST_SSH_DOMAIN`, `PUBLICHOST_FRONTEND_DOMAIN`,
`PUBLICHOST_SSH_HOST`, `PUBLICHOST_SSH_PORT`,  `PUBLICHOST_HOST_KEY_PATH`,

`PUBLICHOST_HTTP_HOST`, `PUBLICHOST_HTTP_PORT` — see the `Settings` docstring in
`src/publichost/config.py`.

Example: `PUBLICHOST_SSH_PORT=2223 uv run python -m publichost`
