---
name: deploy-docker
description: Build and deploy PublicHost via Docker and docker compose, view logs, update
whenToUse: When you need to build a Docker image, start or update the service on a server, or inspect container logs
---

# Docker deployment

Everything goes through `./deploy.sh` (see README "Self-host in one command").
Topology: Traefik inside Compose terminates TLS (Let's Encrypt wildcard via
Cloudflare DNS-01) on 80/443 and routes `<label>.<domain>` → `publichost:8080`,
`<domain>` → `api:8000`. SSH goes straight to the `publichost` container.

## Main commands

- Install / reconfigure: `./deploy.sh` (`--reconfigure`, `--staging`, `-y`)
- Update: `./deploy.sh update` (git pull + rebuild)
- Status / logs: `./deploy.sh status`, `./deploy.sh logs [service]`
- Stop: `./deploy.sh down` (data and host key survive in named volumes)
- Raw compose always needs `--env-file .env.docker` (values are interpolated).

## Post-deployment check

`ssh -p <port> -R 0:localhost:3000 <domain>` → banner with a one-time subdomain;
`https://<domain>/` shows the landing page. After disconnect the logs show
`Released <label>.<base_domain>`.

## Caution

- `./deploy.sh uninstall` / `docker compose down -v` deletes the `hostkey`
  volume and rotates the host key — clients will see an SSH host-key-changed
  warning. Do not do this unless necessary.
- Client SSH port 22 requires moving the host sshd first. Do not change without
  user confirmation.
- Let's Encrypt rate limits: test with `./deploy.sh --staging`.
