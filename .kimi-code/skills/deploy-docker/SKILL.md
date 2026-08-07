---
name: deploy-docker
description: Build and deploy PublicHost via Docker and docker compose, view logs, update
whenToUse: When you need to build a Docker image, start or update the service on a server, or inspect container logs
---

# Docker deployment

Topology: nginx runs on the host outside Docker; the container runs only the
PublicHost SSH server + HTTP reverse proxy. SSH traffic goes straight to the
container, bypassing nginx.

## Main commands

- Build image: `docker build -t publichost-backend .`
- Start: `docker compose up -d`
- Logs: `docker compose logs -f`
- Update: `git pull && docker compose up -d --build`
- Stop: `docker compose down` (host_key survives via named volume `hostkey`)

## Post-deployment check

`ssh -p 2222 <host>` → banner with a one-time subdomain. After disconnect the
logs show `Released <label>.<base_domain>`.

## Caution

- Deleting the `hostkey` volume (`docker compose down -v`) rotates the host key —
  clients will see an SSH host-key-changed warning. Do not do this unless
  necessary.
- Production port: clients expect SSH on 22. Options: map `22:2222` (after moving
  the host sshd to another port) or use a dedicated IP. Do not change without
  user confirmation.
- The healthcheck makes a TCP connection every minute — in the logs this appears
  as an Assigned/Released pair, which is normal.
