# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Release notes on GitHub are
generated from merged pull requests.

## [1.0.0] - Unreleased

First open-source release.

### Added
- `./deploy.sh`: one-command self-hosting on a Linux server (Docker install,
  Cloudflare DNS records, firewall, build, wildcard TLS, smoke checks) plus
  `update`, `status`, `logs`, `restart`, `down`, `uninstall`.
- Traefik in Docker Compose with a Let's Encrypt wildcard certificate via the
  Cloudflare DNS-01 challenge.
- Built-in landing page and WebLogs viewer at `https://<domain>/?token=…`.
- Multi-arch images on GHCR (`ghcr.io/publichost-run/publichost`) and
  `./deploy.sh --image` to run them instead of building locally.
- CI (tests with coverage on Python 3.11–3.13, shellcheck, Docker build),
  Dependabot, issue/PR templates, contributing and security policies.

### Changed
- The WebLogs link in the SSH banner is now `https://<base_domain>/?token=…`.
- `PUBLICHOST_CORS_ORIGINS` defaults to empty.

### Removed
- `PUBLICHOST_FRONTEND_DOMAIN` and the external frontend; the
  `tunnels.frontend_domain` column is dropped on API startup.
- Host nginx and systemd examples (replaced by Traefik in Compose).

[1.0.0]: https://github.com/publichost-run/publichost/releases/tag/v1.0.0
